# base-wallet-risk

x402 micro-service: Base wallet risk analysis (ETH balance, tx count, token balances, heuristic risk score).

## Endpoints
- GET /health (free)
- GET /risk?address=0x... (0.002 USDC)
- GET /tokens?address=0x... (0.002 USDC)

## Run
npm install && PORT=8860 npm start
