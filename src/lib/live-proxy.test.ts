import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
const auth = vi.hoisted(() => ({
  getSession: vi.fn(), getSessionToken: vi.fn(() => ''), isGuestEnabled: vi.fn(() => true),
}));
vi.mock('@/lib/auth', () => auth);
vi.mock('@/lib/permissions-server', () => ({ guestMaySee: () => true }));
import { GET } from '@/app/api/palworld/[...path]/route';
const fetchMock = vi.fn();
const call = (path: string[]) => GET(new NextRequest('http://localhost/api/palworld/info'), { params: Promise.resolve({ path }) });
beforeEach(() => {
  vi.clearAllMocks();
  auth.isGuestEnabled.mockReturnValue(true);
  auth.getSession.mockResolvedValue({ user: null, capabilities: ['view.basic'] });
  vi.stubGlobal('fetch', fetchMock);
});
describe('live API trust boundary', () => {
  it.each([['info', '../players'], ['info/../players'], ['settings'], ['__proto__'], ['players', 'extra']])('refuses unexpected path %s', async (...path) => {
    expect((await call(path)).status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it('honors global guest disable before accessing the privileged server', async () => {
    auth.isGuestEnabled.mockReturnValue(false);
    expect((await call(['players'])).status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it('filters hidden guest rows and explicitly serializes public fields', async () => {
    fetchMock.mockImplementation(async (url: string) => Response.json(url.endsWith('/privacy/hidden') ? { players: ['aabb0000-0000-0000-0000-000000000000'] } : {
      privateMetadata: 'secret', players: [
        { name: 'hidden', playerId: 'AABB', userId: 'steam-hidden' },
        { name: 'visible', playerId: 'CCDD', ip: 'secret', userId: 'steam-secret', newPrivateField: 'secret', level: 7 },
      ],
    }));
    const res = await call(['players']);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ players: [{ name: 'visible', level: 7 }] });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it.each([null, {}, { players: 'not a list' }, { players: [123] }])('fails closed for a malformed privacy response', async (policy) => {
    fetchMock.mockImplementation(async (url: string) => Response.json(url.endsWith('/privacy/hidden') ? policy : { players: [{ name: 'private' }] }));
    const res = await call(['players']);
    expect(res.status).toBe(503);
    expect(JSON.stringify(await res.json())).not.toContain('private');
  });
  it('fails closed when the privacy service fails', async () => {
    fetchMock.mockImplementation(async (url: string) => url.endsWith('/privacy/hidden') ? new Response('', { status: 500 }) : Response.json({ players: [{ name: 'private' }] }));
    expect((await call(['players'])).status).toBe(503);
  });
});

it('withholds an unidentifiable row when hidden players exist', async () => {
  fetchMock.mockImplementation(async (url: string) => Response.json(url.endsWith('/privacy/hidden')
    ? { players: ['aabb0000000000000000000000000000'] }
    : { players: [{ name: 'private', playerId: 'unknown', userId: 'steam-unmapped' }] }));
  expect(await (await call(['players'])).json()).toEqual({ players: [] });
});

it('never forwards nested objects through public player fields', async () => {
  fetchMock.mockImplementation(async (url: string) => Response.json(url.endsWith('/privacy/hidden')
    ? { players: [] }
    : { players: [{ name: 'visible', ping: { userId: 'private' }, level: 7 }] }));
  expect(await (await call(['players'])).json()).toEqual({ players: [{ name: 'visible', level: 7 }] });
});
