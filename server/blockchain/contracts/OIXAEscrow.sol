// SPDX-License-Identifier: MIT
pragma solidity =0.8.28;

/**
 * @title OIXAEscrow
 * @author OIXA Protocol
 * @notice Holds USDC between agent-to-agent transactions on Base mainnet.
 *         The protocol wallet creates escrows, releases on successful delivery,
 *         or refunds on failure. Commission is deducted at release time.
 * @dev Deployed on Base mainnet (chainId 8453).
 *      USDC address: 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
 *
 *      Security fixes (v2):
 *      - CEI pattern enforced in createEscrow: state written BEFORE transferFrom
 *      - pragma pinned to =0.8.28 (avoids 3 known bugs in ^0.8.20)
 */

interface IERC20 {
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
    function allowance(address owner, address spender) external view returns (uint256);
    function balanceOf(address account) external view returns (uint256);
}

contract OIXAEscrow {
    IERC20 public immutable usdc;
    address public immutable protocol;

    enum Status { Active, Released, Refunded }

    struct Escrow {
        address payer;
        address payee;
        uint256 amount;      // total locked (USDC, 6 decimals)
        uint256 commission;  // protocol fee (subset of amount)
        Status  status;
        bytes32 auctionId;
        uint256 createdAt;
    }

    mapping(bytes32 => Escrow) public escrows;

    // Protocol-level stats
    uint256 public totalLocked;
    uint256 public totalReleased;
    uint256 public totalCommissions;
    uint256 public totalRefunded;

    // Emergency pause
    bool public paused;

    // --- Events ---
    event Paused(address indexed by);
    event Unpaused(address indexed by);
    event EscrowCreated(
        bytes32 indexed escrowId,
        bytes32 indexed auctionId,
        address indexed payer,
        address payee,
        uint256 amount,
        uint256 commission
    );
    event EscrowReleased(
        bytes32 indexed escrowId,
        address indexed payee,
        uint256 net,
        uint256 commission
    );
    event EscrowRefunded(
        bytes32 indexed escrowId,
        address indexed payer,
        uint256 amount
    );

    // --- Errors ---
    error EscrowAlreadyExists();
    error EscrowNotFound();
    error AlreadySettled();
    error OnlyProtocol();
    error TransferFailed();
    error InvalidAmount();
    error ContractPaused();

    constructor(address _usdc, address _protocol) {
        require(_usdc    != address(0), "zero usdc");
        require(_protocol != address(0), "zero protocol");
        usdc     = IERC20(_usdc);
        protocol = _protocol;
    }

    modifier onlyProtocol() {
        if (msg.sender != protocol) revert OnlyProtocol();
        _;
    }

    modifier whenNotPaused() {
        if (paused) revert ContractPaused();
        _;
    }

    /// @notice Pause all escrow operations. Only protocol wallet.
    function pause() external onlyProtocol {
        paused = true;
        emit Paused(msg.sender);
    }

    /// @notice Resume escrow operations. Only protocol wallet.
    function unpause() external onlyProtocol {
        paused = false;
        emit Unpaused(msg.sender);
    }

    /**
     * @notice Lock USDC in escrow.
     *         Caller must have approved this contract for `amount` USDC before calling.
     * @param escrowId   Unique ID — keccak256 of the OIXA escrow ID string
     * @param auctionId  Auction this escrow belongs to
     * @param payee      Agent wallet that will receive net payment on success
     * @param amount     Total USDC to lock (6 decimals, e.g. 1 USDC = 1_000_000)
     * @param commission Protocol fee, must be < amount
     *
     * @dev CEI pattern: all state changes happen BEFORE the external transferFrom call.
     *      This eliminates the reentrancy-no-eth finding from Slither audit (2026-03-25).
     */
    function createEscrow(
        bytes32 escrowId,
        bytes32 auctionId,
        address payee,
        uint256 amount,
        uint256 commission
    ) external whenNotPaused {
        // ── Checks ───────────────────────────────────────────────────────────
        if (escrows[escrowId].createdAt != 0) revert EscrowAlreadyExists();
        if (amount == 0)              revert InvalidAmount();
        if (commission >= amount)     revert InvalidAmount();
        if (payee == address(0))      revert InvalidAmount();

        // ── Effects (write state BEFORE external call) ────────────────────────
        escrows[escrowId] = Escrow({
            payer:      msg.sender,
            payee:      payee,
            amount:     amount,
            commission: commission,
            status:     Status.Active,
            auctionId:  auctionId,
            createdAt:  block.timestamp
        });

        totalLocked += amount;

        // ── Interactions (external call last) ─────────────────────────────────
        if (!usdc.transferFrom(msg.sender, address(this), amount)) revert TransferFailed();

        emit EscrowCreated(escrowId, auctionId, msg.sender, payee, amount, commission);
    }

    /**
     * @notice Release escrow on successful output delivery.
     *         Net amount → payee. Commission → protocol wallet.
     *         Only callable by the protocol wallet.
     */
    function release(bytes32 escrowId) external onlyProtocol whenNotPaused {
        Escrow storage e = escrows[escrowId];
        if (e.createdAt == 0)           revert EscrowNotFound();
        if (e.status != Status.Active)  revert AlreadySettled();

        // Effects
        e.status = Status.Released;
        uint256 net = e.amount - e.commission;

        totalLocked      -= e.amount;
        totalReleased    += net;
        totalCommissions += e.commission;

        // Interactions
        if (!usdc.transfer(e.payee, net)) revert TransferFailed();
        if (e.commission > 0) {
            if (!usdc.transfer(protocol, e.commission)) revert TransferFailed();
        }

        emit EscrowReleased(escrowId, e.payee, net, e.commission);
    }

    /**
     * @notice Refund escrow to payer on failed/cancelled delivery.
     *         Only callable by the protocol wallet.
     */
    function refund(bytes32 escrowId) external onlyProtocol whenNotPaused {
        Escrow storage e = escrows[escrowId];
        if (e.createdAt == 0)           revert EscrowNotFound();
        if (e.status != Status.Active)  revert AlreadySettled();

        // Effects
        e.status        = Status.Refunded;
        uint256 amount  = e.amount;
        totalLocked    -= amount;
        totalRefunded  += amount;

        // Interactions
        if (!usdc.transfer(e.payer, amount)) revert TransferFailed();

        emit EscrowRefunded(escrowId, e.payer, amount);
    }

    /// @notice Read escrow details
    function getEscrow(bytes32 escrowId) external view returns (Escrow memory) {
        return escrows[escrowId];
    }

    /// @notice On-chain protocol stats
    function stats() external view returns (
        uint256 locked,
        uint256 released,
        uint256 commissions,
        uint256 refunded
    ) {
        return (totalLocked, totalReleased, totalCommissions, totalRefunded);
    }

    /// @notice Protocol USDC balance held in this contract
    function contractBalance() external view returns (uint256) {
        return usdc.balanceOf(address(this));
    }

    // ── Emergency rescue functions ────────────────────────────────────────────

    /// @notice Recover ETH accidentally sent to this contract.
    ///         Only callable by the protocol wallet.
    /// @param to Recipient address for the rescued ETH
    function rescueETH(address to) external onlyProtocol {
        if (to == address(0)) revert InvalidAmount();
        uint256 bal = address(this).balance;
        if (bal == 0) revert InvalidAmount();
        (bool ok, ) = to.call{value: bal}("");
        require(ok, "ETH transfer failed");
    }

    /// @notice Recover ERC-20 tokens accidentally sent to this contract.
    ///         Cannot be used to drain the USDC held in active escrows —
    ///         only the surplus above `totalLocked` can be rescued.
    ///         Only callable by the protocol wallet.
    /// @param token ERC-20 token address to rescue
    /// @param to    Recipient address
    function rescueERC20(address token, address to) external onlyProtocol {
        if (to == address(0)) revert InvalidAmount();
        IERC20 t = IERC20(token);
        uint256 bal = t.balanceOf(address(this));
        // If rescuing USDC: only allow surplus above locked escrow funds
        if (token == address(usdc)) {
            if (bal <= totalLocked) revert InvalidAmount();
            bal = bal - totalLocked;
        }
        if (bal == 0) revert InvalidAmount();
        if (!t.transfer(to, bal)) revert TransferFailed();
    }

    /// @notice Allow contract to receive ETH (e.g. from refunds or accidental sends)
    receive() external payable {}
}
