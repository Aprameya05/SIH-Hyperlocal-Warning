const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

function riskLabel(p) {
  if (p >= 0.55) return 'HIGH';
  if (p >= 0.35) return 'MODERATE';
  if (p >= 0.15) return 'LOW';
  return 'MINIMAL';
}

function slotName(slot) {
  return ['Late Night (0001-0600 IST)', 'Morning (0601-1200 IST)',
          'Afternoon (1201-1800 IST)', 'Evening (1801-2400 IST)'][slot] ?? `Slot ${slot}`;
}

function htmlBody({ probability, slot, threshold, generated_at, to }) {
  const pct = Math.round(probability * 100);
  const tpct = Math.round(threshold * 100);
  const risk = riskLabel(probability);
  const color = probability >= 0.55 ? '#ef5350' : probability >= 0.35 ? '#ff9800'
                : probability >= 0.15 ? '#ffc107' : '#4caf50';
  return `<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#040711;font-family:monospace;">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;margin:32px auto;">
  <tr><td style="background:#040711;border:1px solid rgba(56,189,248,0.18);border-radius:12px;padding:32px;">
    <div style="color:#38bdf8;font-size:10px;letter-spacing:.2em;margin-bottom:16px;">
      INDIA METEOROLOGICAL DEPARTMENT · STATION 43295 · VOBL/BLR
    </div>
    <div style="color:${color};font-size:48px;font-weight:900;line-height:1;margin-bottom:4px;">
      ${pct}<span style="font-size:22px;">%</span>
    </div>
    <div style="color:${color};font-size:13px;letter-spacing:.15em;margin-bottom:24px;">
      ⚡ ${risk} THUNDERSTORM RISK
    </div>
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr>
        <td style="color:#607d8b;font-size:11px;padding:6px 0;border-bottom:1px solid rgba(56,189,248,0.08);">TIME SLOT</td>
        <td style="color:#cfd8dc;font-size:11px;padding:6px 0;border-bottom:1px solid rgba(56,189,248,0.08);text-align:right;">${slotName(slot)}</td>
      </tr>
      <tr>
        <td style="color:#607d8b;font-size:11px;padding:6px 0;border-bottom:1px solid rgba(56,189,248,0.08);">ALERT THRESHOLD</td>
        <td style="color:#cfd8dc;font-size:11px;padding:6px 0;border-bottom:1px solid rgba(56,189,248,0.08);text-align:right;">${tpct}%</td>
      </tr>
      <tr>
        <td style="color:#607d8b;font-size:11px;padding:6px 0;">GENERATED AT</td>
        <td style="color:#cfd8dc;font-size:11px;padding:6px 0;text-align:right;">${generated_at}</td>
      </tr>
    </table>
    <div style="margin-top:24px;">
      <a href="https://csir-thunderstorm-bengaluru.pages.dev/#dashboard"
         style="display:inline-block;background:${color};color:#000;font-size:11px;font-weight:700;
                letter-spacing:.12em;padding:10px 20px;border-radius:6px;text-decoration:none;">
        VIEW DASHBOARD →
      </a>
    </div>
    <div style="margin-top:24px;color:#37474f;font-size:9px;letter-spacing:.1em;">
      IMD NOWCAST SYSTEM v2.0 · DR. GEETA AGNIHOTRI · SCIENTIST F
    </div>
  </td></tr>
</table>
</body></html>`;
}

export default {
  async fetch(request, env) {
    // CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: CORS });
    }

    if (request.method !== 'POST') {
      return new Response('Method not allowed', { status: 405, headers: CORS });
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return new Response(JSON.stringify({ ok: false, error: 'Invalid JSON' }),
        { status: 400, headers: { ...CORS, 'Content-Type': 'application/json' } });
    }

    const { probability, slot, threshold, generated_at, to } = body;
    const recipient = to || env.DEFAULT_TO;

    if (!recipient) {
      return new Response(JSON.stringify({ ok: false, error: 'No recipient email' }),
        { status: 400, headers: { ...CORS, 'Content-Type': 'application/json' } });
    }

    const pct = Math.round((probability ?? 0) * 100);
    const risk = riskLabel(probability ?? 0);

    const sgPayload = {
      personalizations: [{ to: [{ email: recipient }] }],
      from: { email: env.FROM_EMAIL, name: 'IMD Nowcast · Station 43295' },
      subject: `⚡ ${risk} RISK ${pct}% — IMD Thunderstorm Alert (VOBL/BLR)`,
      content: [{ type: 'text/html', value: htmlBody({ probability, slot, threshold, generated_at, to: recipient }) }],
    };

    const sgRes = await fetch('https://api.sendgrid.com/v3/mail/send', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${env.SENDGRID_API_KEY}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(sgPayload),
    });

    if (sgRes.ok || sgRes.status === 202) {
      return new Response(JSON.stringify({ ok: true }),
        { headers: { ...CORS, 'Content-Type': 'application/json' } });
    }

    const errText = await sgRes.text();
    return new Response(JSON.stringify({ ok: false, error: errText }),
      { status: 502, headers: { ...CORS, 'Content-Type': 'application/json' } });
  },
};
