// k6 load script for paykeeper POST /v1/charges.
//
// Mix of fresh and replayed keys: ~70% of iterations create a new charge,
// ~30% replay one of the recently-seen keys. The server must return 201
// with the same body for a replayed key — never 500, never two charge
// rows with the same operation_id.
//
// Run with:   k6 run load/charges.js
// Override:   k6 run -e BASE_URL=http://localhost:8000 -e DURATION=30s load/charges.js
// Then verify no duplicates:
//   docker compose exec -T postgres psql -U paykeeper -d paykeeper -f /load/duplicate_check.sql

import http from 'k6/http';
import { check, sleep } from 'k6';
import { SharedArray } from 'k6/data';
import { randomString } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const DURATION = __ENV.DURATION || '30s';
const VUS = parseInt(__ENV.VUS || '20', 10);

export const options = {
  vus: VUS,
  duration: DURATION,
  thresholds: {
    http_req_duration: ['p(95)<500'],
    http_req_failed: ['rate<0.01'],
    checks: ['rate>0.99'],
  },
};

// Fixed pool of idempotency keys built once at startup. VUs share this pool so replays
// genuinely overlap across concurrent workers.
const KEY_POOL_SIZE = 2000;
const keyPool = new SharedArray('keys', function () {
  const out = [];
  for (let i = 0; i < KEY_POOL_SIZE; i++) {
    out.push(`k-${randomString(16)}`);
  }
  return out;
});

export default function () {
  // Decide whether this iteration is fresh (70%) or a replay (30%).
  const replay = Math.random() < 0.3;
  const key = replay
    ? keyPool[Math.floor(Math.random() * keyPool.length)]
    : `k-${__VU}-${__ITER}-${randomString(8)}`;

  const body = JSON.stringify({
    amount: 1000,
    currency: 'USD',
    customer_id: `cust_${__VU}`,
    payment_method_token: 'pm_card_ok',
    description: 'load test',
  });

  const res = http.post(`${BASE_URL}/v1/charges`, body, {
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': key,
    },
    timeout: '5s',
  });

  check(res, {
    'status is 201': (r) => r.status === 201,
    'has charge id': (r) => {
      try {
        return typeof r.json('id') === 'string';
      } catch {
        return false;
      }
    },
  });

  sleep(0.01);
}
