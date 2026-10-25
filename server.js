const express = require('express');
const { paymentMiddleware } = require('x402-express');
const { createPublicClient, http, parseAbi } = require('viem');
const { base } = require('viem/chains');

const PAY_TO = '0x65aa59025C4AcD8CD7ba2051ecb9152abaE4fcC6';
const PRICE = '0.002'; // USDC per call
const PORT = process.env.PORT || 8860;

const client = createPublicClient({ chain: base, transport: http('https://mainnet.base.org') });

const ERC20_ABI = parseAbi([
  'function balanceOf(address) view returns (uint256)',
  'function decimals() view returns (uint8)',
  'function symbol() view returns (string)',
  'function name() view returns (string)',
]);

const app = express();
app.use(express.json());

app.use(paymentMiddleware(PAY_TO, {
  'GET /risk': { price: PRICE, network: 'base' },
  'GET /tokens': { price: PRICE, network: 'base' },
}));

app.get('/health', (req, res) => res.json({ ok: true, service: 'base-wallet-risk', payTo: PAY_TO, price: PRICE }));

async function ethBalance(addr) {
  const bal = await client.getBalance({ address: addr });
  return Number(bal) / 1e18;
}

app.get('/risk', async (req, res) => {
  const addr = (req.query.address || '').trim();
  if (!/^0x[a-fA-F0-9]{40}$/.test(addr)) return res.status(400).json({ error: 'invalid address' });
  try {
    const [bal, txCount, block] = await Promise.all([
      ethBalance(addr),
      client.getTransactionCount({ address: addr }),
      client.getBlockNumber(),
    ]);
    // Heuristic risk score 0-100 (higher = riskier)
    let score = 0;
    const flags = [];
    if (bal < 0.001) { score += 25; flags.push('low_eth_balance'); }
    if (txCount === 0) { score += 20; flags.push('no_history'); }
    if (txCount > 1000) { score += 10; flags.push('high_activity'); }
    const risk = score >= 40 ? 'high' : score >= 20 ? 'medium' : 'low';
    res.json({
      address: addr,
      ethBalance: bal,
      txCount,
      latestBlock: Number(block),
      riskScore: score,
      riskLevel: risk,
      flags,
      chain: 'base',
    });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.get('/tokens', async (req, res) => {
  const addr = (req.query.address || '').trim();
  if (!/^0x[a-fA-F0-9]{40}$/.test(addr)) return res.status(400).json({ error: 'invalid address' });
  const tokens = [
    '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', // USDC
    '0x4200000000000000000000000000000000000006', // WETH
  ];
  try {
    const out = [];
    for (const t of tokens) {
      const [bal, sym, dec] = await Promise.all([
        client.readContract({ address: t, abi: ERC20_ABI, functionName: 'balanceOf', args: [addr] }),
        client.readContract({ address: t, abi: ERC20_ABI, functionName: 'symbol' }),
        client.readContract({ address: t, abi: ERC20_ABI, functionName: 'decimals' }),
      ]);
      out.push({ token: t, symbol: sym, balance: Number(bal) / 10 ** Number(dec), decimals: Number(dec) });
    }
    res.json({ address: addr, tokens: out, chain: 'base' });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.listen(PORT, () => console.log(`base-wallet-risk listening on ${PORT}`));
