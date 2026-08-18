/**
 * CSRF gate (AUDIT S7): `crossSiteReason` decides whether a mutating request
 * came from another site's page. Pure over Headers, so every branch runs
 * without the framework.
 *
 * The load-bearing negative cases are the ones that must NOT be refused:
 * curl sends neither header, and behind a reverse proxy the plain Host is
 * the internal name — refusing either would break every legitimate
 * deployment the moment the gate shipped, which is how a security check gets
 * reverted.
 */
import { describe, expect, it } from 'vitest';
import { crossSiteReason } from './auth';

const h = (pairs: Record<string, string>) => new Headers(pairs);

describe('crossSiteReason', () => {
  it('refuses the browser-attested cross-site case outright', () => {
    expect(
      crossSiteReason(h({ 'sec-fetch-site': 'cross-site' }))
    ).toContain('cross-site');
  });

  it('passes same-origin, same-site and user-initiated fetch metadata', () => {
    for (const site of ['same-origin', 'same-site', 'none']) {
      expect(crossSiteReason(h({ 'sec-fetch-site': site }))).toBeNull();
    }
  });

  it('passes a request with neither header — curl cannot be CSRF-ed', () => {
    expect(crossSiteReason(h({}))).toBeNull();
  });

  it('refuses Origin: null — sandboxed iframes and file:// pages', () => {
    expect(crossSiteReason(h({ origin: 'null' }))).toContain('null');
  });

  it('refuses an Origin that does not match the host', () => {
    const reason = crossSiteReason(
      h({ origin: 'https://evil.example', host: 'dash.lan:3000' })
    );
    expect(reason).toContain('evil.example');
  });

  it('passes a matching Origin, case-insensitively', () => {
    expect(
      crossSiteReason(h({ origin: 'http://Dash.LAN:3000', host: 'dash.lan:3000' }))
    ).toBeNull();
  });

  it('compares against X-Forwarded-Host first — the reverse-proxy case', () => {
    // Behind a proxy the plain Host is the internal name; the public Origin
    // must be judged against the public host or every real request fails.
    expect(
      crossSiteReason(h({
        origin: 'https://dash.example.com',
        'x-forwarded-host': 'dash.example.com',
        host: '127.0.0.1:3000',
      }))
    ).toBeNull();
  });

  it('refuses an unparseable Origin rather than waving it through', () => {
    expect(
      crossSiteReason(h({ origin: '::not a url::', host: 'dash.lan' }))
    ).toContain('unparseable');
  });

  it('a forged benign Sec-Fetch-Site does not bypass the Origin check', () => {
    // A non-browser attacker can send any headers it likes — but then it is
    // not a CSRF (no victim cookie). This pins that the two checks are
    // independent: a mismatched Origin still refuses even beside a benign
    // fetch-site value.
    expect(
      crossSiteReason(h({
        'sec-fetch-site': 'same-origin',
        origin: 'https://evil.example',
        host: 'dash.lan',
      }))
    ).toContain('evil.example');
  });
});
