// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "forge-std/Script.sol";

// Minimal import so forge can find the contract
interface IOIXAEscrow {}

contract DeployEscrow is Script {
    function run() external {
        address usdc     = vm.envAddress("USDC_ADDRESS");
        address protocol = vm.envAddress("PROTOCOL_ADDRESS");

        vm.startBroadcast();

        // Deploy via raw bytecode loaded from artifact
        bytes memory bytecode = abi.encodePacked(
            vm.getCode("OIXAEscrow.sol:OIXAEscrow"),
            abi.encode(usdc, protocol)
        );

        address deployed;
        assembly {
            deployed := create(0, add(bytecode, 0x20), mload(bytecode))
        }
        require(deployed != address(0), "Deploy failed");

        vm.stopBroadcast();

        console.log("OIXAEscrow deployed to:", deployed);
    }
}
