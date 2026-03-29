// BONHEURBOT AI PRO - SMART MINING VERSION (R25 SAFE MODE)

// ================= BACKEND (Node.js + Express) =================

const express = require('express');
const WebSocket = require('ws');
const cors = require('cors');

const app = express();
app.use(cors());
app.use(express.json());

let userData = {
  token: '',
  balance: 0,
  trades: [],
  lastDigits: [],
  wins: 0,
  losses: 0
};

let ws;

function connectDeriv(token) {
  ws = new WebSocket('wss://ws.binaryws.com/websockets/v3?app_id=1089');

  ws.onopen = () => {
    ws.send(JSON.stringify({ authorize: token }));
    ws.send(JSON.stringify({ ticks: 'R_25' }));
  };

  ws.onmessage = (msg) => {
    const data = JSON.parse(msg.data);

    if (data.authorize) {
      userData.balance = data.authorize.balance;
    }

    // Collect last digits for analysis
    if (data.tick) {
      const digit = parseInt(data.tick.quote.toString().slice(-1));
      userData.lastDigits.push(digit);

      if (userData.lastDigits.length > 20) {
        userData.lastDigits.shift();
      }
    }

    if (data.buy) {
      userData.trades.push({
        id: Date.now(),
        contract: data.buy.contract_id,
        status: 'OPEN'
      });
    }

    if (data.proposal_open_contract) {
      if (data.proposal_open_contract.status === 'won') userData.wins++;
      if (data.proposal_open_contract.status === 'lost') userData.losses++;
    }
  };
}

// ================= SMART TREND STRATEGY =================

function detectTrend() {
  const digits = userData.lastDigits;
  if (digits.length < 10) return null;

  let up = 0;
  let down = 0;

  for (let i = 1; i < digits.length; i++) {
    if (digits[i] > digits[i - 1]) up++;
    else if (digits[i] < digits[i - 1]) down++;
  }

  if (up > down + 3) return 'UP';
  if (down > up + 3) return 'DOWN';

  return null; // no clear trend
}

function smartMiningStrategy() {
  if (!ws) return;

  const trend = detectTrend();

  // SAFE MODE: trade only if strong trend
  if (!trend) {
    console.log('No clear trend - waiting...');
    return;
  }

  const stake = Math.max(0.35, userData.balance * 0.01);

  const contract_type = trend === 'UP' ? 'CALL' : 'PUT';

  const trade = {
    buy: 1,
    price: stake,
    parameters: {
      amount: stake,
      basis: 'stake',
      contract_type,
      currency: 'USD',
      duration: 1,
      duration_unit: 'm',
      symbol: 'R_25'
    }
  };

  console.log('Trading with trend:', trend);
  ws.send(JSON.stringify(trade));
}

// Run every 20 seconds (safe mining mode)
setInterval(smartMiningStrategy, 20000);

// ================= API =================

app.post('/connect', (req, res) => {
  const { token } = req.body;
  userData.token = token;
  connectDeriv(token);
  res.json({ status: 'connected' });
});

app.get('/balance', (req, res) => {
  res.json({ balance: userData.balance });
});

app.get('/stats', (req, res) => {
  const total = userData.wins + userData.losses;
  const winrate = total ? ((userData.wins / total) * 100).toFixed(2) : 0;

  res.json({
    wins: userData.wins,
    losses: userData.losses,
    winrate
  });
});

app.get('/trades', (req, res) => {
  res.json(userData.trades);
});

app.listen(5000, () => console.log('BonheurBot AI PRO running'));


// ================= FRONTEND (React Dashboard PRO) =================

import React, { useState, useEffect } from 'react';

export default function BonheurBot() {
  const [token, setToken] = useState('');
  const [balance, setBalance] = useState(0);
  const [stats, setStats] = useState({ wins: 0, losses: 0, winrate: 0 });
  const [trades, setTrades] = useState([]);

  const connectAPI = async () => {
    await fetch('http://localhost:5000/connect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token })
    });
  };

  const loadData = async () => {
    const b = await fetch('http://localhost:5000/balance').then(r => r.json());
    const s = await fetch('http://localhost:5000/stats').then(r => r.json());
    const t = await fetch('http://localhost:5000/trades').then(r => r.json());

    setBalance(b.balance);
    setStats(s);
    setTrades(t);
  };

  useEffect(() => {
    setInterval(loadData, 5000);
  }, []);

  return (
    <div className="p-6">
      <h1 className="text-3xl font-bold">🤖 BonheurBot AI PRO (R25 Mining Mode)</h1>

      <div className="mt-4">
        <input
          placeholder="Enter Deriv API Token"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          className="border p-2 mr-2"
        />
        <button onClick={connectAPI} className="bg-green-600 text-white p-2">
          Connect Bot
        </button>
      </div>

      <div className="mt-6">
        <h2>💰 Balance: ${balance}</h2>
        <h3>📊 Winrate: {stats.winrate}%</h3>
        <h3>✅ Wins: {stats.wins} | ❌ Losses: {stats.losses}</h3>
      </div>

      <div className="mt-6">
        <h2>📜 Trade History</h2>
        <ul>
          {trades.map((t) => (
            <li key={t.id}>{t.contract} - {t.status}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}
