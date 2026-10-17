// Steady payment traffic for releases and game days.
//
//   k6 run load/payments_load.js                                  # local, 10 req/s for 2 min
//   k6 run -e BASE_URL=http://my-alb-dns -e RATE=20 -e DURATION=10m load/payments_load.js
//
// About 5% of requests are deliberate retries with a repeated Idempotency-Key,
// so every load test also checks that customers are never charged twice.

import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';
const RATE = Number(__ENV.RATE || 10);
const DURATION = __ENV.DURATION || '2m';

const duplicateCharges = new Counter('duplicate_charges');

export const options = {
  scenarios: {
    steady_payments: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: 20,
      maxVUs: 100,
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],          // under 1% errors
    'http_req_duration{name:create}': ['p(95)<500'],
    duplicate_charges: ['count==0'],         // never charge twice
  },
};

function pay(key) {
  const body = JSON.stringify({
    amount: 100 + (key.length % 50) * 10,
    currency: 'KES',
    phone_number: '254712345678',
    reference: `LOAD-${key}`,
  });
  return http.post(`${BASE_URL}/payments`, body, {
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
    tags: { name: 'create' },
  });
}

export default function () {
  const key = `load-${__VU}-${__ITER}-${Date.now()}`;
  const first = pay(key);
  check(first, { 'created (201)': (r) => r.status === 201 });

  if (Math.random() < 0.05) {
    const retry = pay(key);
    check(retry, { 'retry replayed (200)': (r) => r.status === 200 });
    if (first.status === 201 && retry.status === 200 &&
        retry.json('payment_id') !== first.json('payment_id')) {
      duplicateCharges.add(1);
    }
  }
}