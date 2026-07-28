import { TwilioHealth } from "../TwilioHealth";
import { C, Panel } from "../components";

export function TwilioHealthPage() {
  return (
    <div>
      <h2 style={{ fontSize: "1.125rem", fontWeight: 600, marginBottom: 4 }}>
        Twilio · Multi-Account Health
      </h2>
      <div style={{ color: C.muted, fontSize: "0.75rem", marginBottom: 16 }}>
        Live status of every configured Twilio account. Click <strong>demo rotation</strong>{" "}
        on any account to force a 30s cooldown on it and place a real test call — the response
        below shows which account the rotator picked as a fallback. This is the proof that
        calls are rotating across accounts, not stuck on a single one.
      </div>
      <TwilioHealth pollMs={4000} />
      <Panel title="Why multiple Twilio accounts?">
        <div style={{ fontSize: "0.8125rem", color: C.muted, lineHeight: 1.6 }}>
          <p style={{ margin: "0 0 8px 0" }}>
            <strong>Free-tier constraints.</strong> Each Twilio trial account can verify
            at most 5 caller IDs. Aarogya Bandhu calls 30+ patients per day, so a single
            account caps out fast. The rotator round-robins across accounts and on 429/5xx
            puts the failing account on cooldown for 60s before trying the next.
          </p>
          <p style={{ margin: "0 0 8px 0" }}>
            <strong>Per-account allowlist.</strong> Only numbers in an account's allowlist
            can be dialled from that account. The rotator picks the first account whose
            allowlist contains the target number — so any verified number on any account
            is diallable.
          </p>
          <p style={{ margin: 0 }}>
            <strong>Live cooldown.</strong> Click <strong>demo rotation</strong> on any
            account card to force a 30s cooldown on it, then place a real call. The
            rotator's response shows the order accounts were tried in, and which one
            won.
          </p>
        </div>
      </Panel>
    </div>
  );
}
