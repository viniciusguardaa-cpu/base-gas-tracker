const express = require('express');
const { paymentMiddleware } = require('x402-express');
const { createPublicClient, http, formatGwei, parseGwei } = require('viem');
const { base } = require('viem/chains');

const PAY_TO = '0x65aa59025C4AcD8CD7ba2051ecb9152abaE4fcC6';
const PRICE = '0.001'; // USDC per call
const PORT = process.env.PORT || 8870;

const client = createPublicClient({ chain: base, transport: http('https://mainnet.base.org') });

const app = express();
app.use(express.json());

app.use(paymentMiddleware(PAY_TO, {
  'GET /gas': { price: PRICE, network: 'base' },
  'GET /estimate': { price: PRICE, network: 'base' },
}));

app.get('/health', (req, res) => res.json({ ok: true, service: 'base-gas-tracker', payTo: PAY_TO, price: PRICE, chain: 'base' }));

// Gas price snapshot: base fee, priority fee, suggested maxFeePerGas
app.get('/gas', async (req, res) => {
  try {
    const [block, fees] = await Promise.all([
      client.getBlock({ blockTag: 'latest' }),
      client.estimateFeesPerGas(),
    ]);
    const baseFee = block.baseFeePerGas ? Number(block.baseFeePerGas) / 1e9 : null;
    const priority = fees.maxPriorityFeePerGas ? Number(fees.maxPriorityFeePerGas) / 1e9 : null;
    const maxFee = fees.maxFeePerGas ? Number(fees.maxFeePerGas) / 1e9 : null;
    res.json({
      chain: 'base',
      blockNumber: Number(block.number),
      baseFeeGwei: baseFee,
      maxPriorityFeeGwei: priority,
      maxFeePerGasGwei: maxFee,
      gasUsed: Number(block.gasUsed),
      gasLimit: Number(block.gasLimit),
      utilizationPct: Number((block.gasUsed * 100n) / block.gasLimit),
      timestamp: new Date(Number(block.timestamp) * 1000).toISOString(),
    });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Estimate cost of a transaction given gas limit
app.get('/estimate', async (req, res) => {
  const gasLimit = parseInt(req.query.gasLimit || '21000', 10);
  if (!Number.isFinite(gasLimit) || gasLimit <= 0 || gasLimit > 30000000) {
    return res.status(400).json({ error: 'invalid gasLimit (1..30000000)' });
  }
  try {
    const fees = await client.estimateFeesPerGas();
    const maxFee = fees.maxFeePerGas ? Number(fees.maxFeePerGas) : 0;
    const costWei = BigInt(gasLimit) * maxFee;
    const costEth = Number(costWei) / 1e18;
    // ETH price via public Coinbase endpoint (best-effort)
    let ethUsd = null;
    try {
      const r = await fetch('https://api.coinbase.com/v2/prices/ETH-USD/spot');
      const j = await r.json();
      ethUsd = parseFloat(j?.data?.amount);
    } catch (_) {}
    res.json({
      chain: 'base',
      gasLimit,
      maxFeePerGasGwei: maxFee / 1e9,
      estimatedCostEth: costEth,
      estimatedCostUsd: ethUsd ? costEth * ethUsd : null,
      ethUsd,
    });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.listen(PORT, () => console.log(`base-gas-tracker listening on ${PORT}`));
