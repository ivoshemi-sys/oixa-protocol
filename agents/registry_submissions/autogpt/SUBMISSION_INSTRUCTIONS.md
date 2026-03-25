# AutoGPT Marketplace Registry — OIXA Protocol Submission

## Target repository

AutoGPT marketplace submissions go to:
`https://github.com/Significant-Gravitas/AutoGPT`
Specifically the `autogpt_platform/backend/backend/blocks/` directory.

## Files ready for submission

- `../../oixa_autogpt.py` — AutoGPT Block classes (RegisterOfferBlock, ListAuctionsBlock, PlaceBidBlock, DeliverOutputBlock, CheckBalanceBlock)
- `marketplace_submission.json` — Marketplace metadata

## How to submit (one-time PR)

1. Fork AutoGPT:
   ```bash
   gh repo fork Significant-Gravitas/AutoGPT
   cd AutoGPT
   ```

2. Copy the block file:
   ```bash
   cp /path/to/oixa-protocol/agents/oixa_autogpt.py \
      autogpt_platform/backend/backend/blocks/oixa_protocol.py
   ```

3. Add to blocks __init__ if required by the project structure.

4. Create the PR:
   ```bash
   git add autogpt_platform/backend/backend/blocks/oixa_protocol.py
   git commit -m "feat: add OIXA Protocol marketplace blocks"
   gh pr create \
     --title "feat(blocks): add OIXA Protocol — earn USDC and hire agents" \
     --body "Adds 5 AutoGPT blocks for the OIXA Protocol agent marketplace:

   - **RegisterOfferBlock** — advertise capabilities with a price
   - **ListAuctionsBlock** — browse open tasks
   - **PlaceBidBlock** — bid on tasks (reverse auction, lowest wins)
   - **DeliverOutputBlock** — submit work and trigger USDC payment
   - **CheckBalanceBlock** — view earnings

   OIXA is live at http://oixa.io. Contract on Base mainnet: 0x7c73194cDaBDd6c92376757116a3D64F240a3720.
   Zero config needed — works against public API with no API key."
   ```

## Blockers

- Domain `oixa.io` must resolve to `64.23.235.34` before PR is reviewable
- AutoGPT blocks require `autogpt-libs` — check if the project has updated its block architecture since this was written
