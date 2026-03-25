# OIXA Protocol — Security Audit Report

**Initial audit:** 2026-03-20 | **v2 fixes applied:** 2026-03-25
**Contract:** `server/blockchain/contracts/OIXAEscrow.sol`
**Auditor:** Automated (Slither) + Manual Review
**Network:** Base Mainnet (chainId 8453)
**Contract Address (v1, retired):** 0x2EF904b07852Bb8103adad65bC799B325c667EF1
**Contract Address (v2, LIVE):** 0x7c73194cDaBDd6c92376757116a3D64F240a3720 — deployed 2026-03-25 on Base mainnet

## Changes in v2 (2026-03-25)

- **CEI fix:** `createEscrow` now writes state (`escrows[escrowId]`, `totalLocked`) BEFORE calling `usdc.transferFrom`. Eliminates `reentrancy-no-eth` and `reentrancy-benign` findings.
- **pragma pinned:** `^0.8.20` → `=0.8.28`. Eliminates 3 known compiler bugs.
- **Slither results:** 6 findings → 3 findings (all remaining are `reentrancy-events`, informational only).

---

## Summary (v2)

| Severity | Count | Status |
|----------|-------|--------|
| Critical | 0 | — |
| High | 1 | Acknowledged |
| Medium | 2 | Acknowledged |
| Low | 3 | Acknowledged |
| Informational | 4 | — |

---

## Findings

### [HIGH-01] Centralization Risk — Single Protocol Wallet Controls All Escrows

**Location:** `OIXAEscrow.sol:87-89, 152, 176`
**Description:**
The `onlyProtocol` modifier restricts `release()` and `refund()` to a single EOA (`protocol` address set at deploy time). If the protocol private key is compromised, an attacker can:
- Release all escrows to arbitrary payees (if the payer changes their approved payee)
- Actually, release/refund goes to stored `e.payee`/`e.payer` so funds can't be redirected — this mitigates severity significantly

**Actual risk:** If private key is stolen, attacker can:
- Prematurely release or refund any escrow
- Disrupt ongoing transactions, cause financial losses from incorrect releases

**Recommendation:**
- Migrate `protocol` role to a Gnosis Safe multisig (2-of-3 or 3-of-5) on Base
- Safe setup already implemented at `server/blockchain/safe_setup.py`
- Assign `SAFE_ADDRESS` in `.env` and update `protocol` address in contract

**Status:** Partially mitigated — `safe_setup.py` exists. Full migration to Safe pending wallet setup.

---

### [MEDIUM-01] No Timeout / Expiry on Escrows

**Location:** `OIXAEscrow.sol:27-35`
**Description:**
Escrows have no expiry timestamp. If the protocol wallet is lost or goes offline, funds could be locked forever. The `createdAt` field exists but there is no on-chain enforcement of a release window.

**Recommendation:**
Add a `deadline` field to `Escrow` struct. Allow payer to self-refund after `deadline` if no release has occurred.

```solidity
uint256 deadline; // set to block.timestamp + ESCROW_TIMEOUT at creation

function selfRefundExpired(bytes32 escrowId) external {
    Escrow storage e = escrows[escrowId];
    require(e.status == Status.Active, "already settled");
    require(block.timestamp > e.deadline, "not expired");
    require(msg.sender == e.payer, "not payer");
    // ... refund logic
}
```

**Status:** Open — not yet implemented.

---

### [MEDIUM-02] No Reentrancy Guard on `release()` and `refund()`

**Location:** `OIXAEscrow.sol:152-169, 176-188`
**Description:**
`release()` and `refund()` call `usdc.transfer()` after setting `e.status = Status.Released/Refunded`. This follows the checks-effects-interactions pattern correctly. However, there is no explicit `ReentrancyGuard`.

For ERC-20 tokens this is generally safe (USDC does not have reentrant callbacks), but a non-standard token implementation could trigger reentrancy.

**Recommendation:**
Add OpenZeppelin's `ReentrancyGuard` as a defense-in-depth measure.

```solidity
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
contract OIXAEscrow is ReentrancyGuard {
    function release(bytes32 escrowId) external onlyProtocol whenNotPaused nonReentrant { ... }
    function refund(bytes32 escrowId) external onlyProtocol whenNotPaused nonReentrant { ... }
}
```

**Status:** Open — low practical risk with USDC, but recommended for defense-in-depth.

---

### [LOW-01] Integer Overflow on Protocol Stats Counters (Minor)

**Location:** `OIXAEscrow.sol:40-43`
**Description:**
`totalLocked`, `totalReleased`, `totalCommissions`, `totalRefunded` are `uint256` and accumulate without bound. In Solidity ^0.8.x, overflow reverts automatically. No practical issue, but USDC with 6 decimals could theoretically saturate at ~1.8 × 10^67 USDC.

**Recommendation:** No action needed — overflow protection is built into Solidity 0.8+.

---

### [LOW-02] `createEscrow` Callable by Any Address (Not Just Protocol)

**Location:** `OIXAEscrow.sol:118-145`
**Description:**
`createEscrow` has `whenNotPaused` but not `onlyProtocol`. Any address that has approved the contract for USDC can create an escrow. The escrow's `payer` is set to `msg.sender`.

**Actual risk:** Low. Anyone can lock their own USDC with a custom `payee`. Only the protocol wallet can then release/refund it. This could be exploited for griefing (locking funds permanently if protocol key is lost) but not for stealing funds.

**Recommendation:**
If the intent is to only allow protocol-initiated escrows, add `onlyProtocol` to `createEscrow`. If permissionless escrow creation is desired (any payer can lock funds), add a self-refund mechanism as described in MEDIUM-01.

**Status:** Design decision — document intent clearly.

---

### [LOW-03] Pause Does Not Protect `contractBalance()` — No Front-Running Risk

**Location:** `OIXAEscrow.sol:207-209`
**Description:**
View function — no risk. Informational only.

---

### [INFO-01] No Upgradeability

The contract is not upgradeable (no proxy pattern). If a bug is found post-deployment, a new contract must be deployed and all funds migrated. **This is by design** — immutability increases security.

### [INFO-02] Commission Transferred Twice in Edge Case

**Location:** `OIXAEscrow.sol:164-167`
In `release()`: if `usdc.transfer(e.payee, net)` succeeds but `usdc.transfer(protocol, commission)` fails (e.g., protocol address blacklisted by USDC), the first transfer is irreversible. Probability: extremely low with USDC, but worth noting.

### [INFO-03] No Events for Pause State Read

`paused` is publicly readable but there's no getter that reveals when it was last paused. The `Paused`/`Unpaused` events cover this sufficiently.

### [INFO-04] `transferFrom` Return Value Checked Correctly

The contract correctly reverts on `!usdc.transferFrom(...)` and `!usdc.transfer(...)`. This is compliant with ERC-20 spec. USDC reverts on failure but the check is still correct practice.

---

## Slither Static Analysis — v2 Results (run 2026-03-25 on VPS, fixed contract)

**Key result: 6 findings → 3 findings after CEI fix and pragma pin.**

### Eliminated findings (v1 → v2)
- ~~`reentrancy-no-eth`~~ (Medium) — **FIXED** by CEI reorder in `createEscrow`
- ~~`reentrancy-benign`~~ (Low) — **FIXED** by CEI reorder (`totalLocked` now before external call)
- ~~`solc-version`~~ (Informational) — **FIXED** by pinning to `=0.8.28`

### Remaining findings (v2, all informational)

**`reentrancy-events` (3x, Informational)**
Events emitted after external calls in `createEscrow`, `release`, `refund`. This is standard pattern in Solidity event emission. No financial risk — events can be emitted after transfers but this doesn't affect state integrity. All critical state changes happen before the external calls (CEI enforced).

No further action required.

---

## Slither Static Analysis — v1 Results (historical, run 2026-03-25 on VPS)

```
Tool: slither-analyzer (in venv)
solc: 0.8.20
Target: OIXAEscrow.sol
Contracts analyzed: 2 (OIXAEscrow + IERC20 interface)
Results: 6 findings
```

### Slither Finding 1 — reentrancy-no-eth (Medium)

**Function:** `createEscrow()`
External call `usdc.transferFrom()` at line 130 **before** state variable `escrows[escrowId]` is written (line 132-140). Cross-function reentrancy possible via `createEscrow`, `getEscrow`, `refund`, `release`.

**Severity:** Medium (no-eth reentrancy — Slither classification)
**Assessment:** Low practical risk with USDC (doesn't have reentrant callbacks). But pattern violates CEI (Checks-Effects-Interactions). The `escrows[escrowId].createdAt != 0` guard at line 125 prevents double-creation, which partially mitigates this.
**Fix:** Move `escrows[escrowId] = ...` and `totalLocked += amount` **before** the `transferFrom` call. Pattern would become: check → effect → interact.

### Slither Finding 2 — reentrancy-benign (Low)

**Function:** `createEscrow()`
`totalLocked += amount` (line 142) written after external call. Same root cause as Finding 1 but affects only a stat counter — benign.

### Slither Finding 3 — reentrancy-events (Informational)

Events emitted after external calls in `createEscrow`, `release`, `refund`. Standard pattern — no financial risk, but events could fire after a reentrancy-induced revert in theory.

### Slither Finding 4 — solc-version (Informational)

`^0.8.20` has 3 known compiler bugs:
- `VerbatimInvalidDeduplication`
- `FullInlinerNonExpressionSplitArgumentEvaluationOrder`
- `MissingSideEffectsOnSelectorAccess`

None of these affect this contract's specific patterns. **Recommendation:** Pin to `=0.8.28` (latest stable, all three bugs fixed).

---

**Updated severity table after Slither:**

| Severity | Count |
|----------|-------|
| Critical | 0 |
| High | 1 (centralization — Slither doesn't catch this) |
| Medium | 3 (no expiry + no reentrancy guard + CEI violation in createEscrow) |
| Low | 3 |
| Informational | 6 |

---

## Mythril Dynamic Analysis

Mythril requires Docker or a local install with `solc`. Recommended command:

```bash
docker run -v $(pwd):/tmp mythril/myth analyze /tmp/server/blockchain/contracts/OIXAEscrow.sol
```

Estimated findings based on manual analysis: consistent with findings above (centralization, no reentrancy guard).

---

## Risk Summary

The contract follows good practices (CEI pattern, custom errors, events, pause mechanism). The main risk is operational: a single private key controls all escrows. Migrating to a Gnosis Safe (already scaffolded) is the highest-priority security improvement.

**Recommended actions in priority order:**

1. **Migrate `protocol` address to Gnosis Safe** (HIGH-01) — do this before significant TVL
2. **Add escrow expiry + self-refund** (MEDIUM-01) — protects users if protocol goes offline
3. **Add ReentrancyGuard** (MEDIUM-02) — defense in depth, minimal gas overhead
4. **Restrict createEscrow to onlyProtocol** (LOW-02) — if permissionless is not desired

---

*OIXA Protocol Security — Ivan Shemi — 2026-03-20*
