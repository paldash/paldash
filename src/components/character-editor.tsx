'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { PenLine, Search, ShieldCheck, AlertTriangle, Wand2, Undo2 } from 'lucide-react';
import {
  getEditSchema, previewPalEdit, applyPalEdit, getPals,
  previewPlayerEdit, applyPlayerEdit, getSavePlayers, type PalRecord,
} from '@/lib/save-api';
import type { EditSchema, EditPlan, PlayerSaveData } from '@/lib/types';

type Mode = 'pal' | 'player';

/** The two subject types share enough shape to drive one editor. */
interface Subject {
  id: string;
  title: string;
  subtitle: string;
  seed: Record<string, string | number>;
}

/**
 * Character editor — Pals and players.
 *
 * Three-step by design, mirroring the backend: change fields → preview the exact
 * diff → apply. The apply carries the preview's `planHash`, and the backend
 * re-plans and refuses if the world moved in between, so what gets written is
 * always what was shown.
 *
 * The editor renders itself from the backend's schema rather than a second copy
 * of the bounds. If the level cap changes, this UI follows without a code change.
 */
export default function CharacterEditor({ canEdit }: { canEdit: boolean }) {
  const [mode, setMode] = useState<Mode>('pal');
  const [schema, setSchema] = useState<EditSchema | null>(null);
  const [pals, setPals] = useState<PalRecord[]>([]);
  const [players, setPlayers] = useState<PlayerSaveData[]>([]);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<Subject | null>(null);
  const [draft, setDraft] = useState<Record<string, string | number>>({});
  const [plan, setPlan] = useState<EditPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getEditSchema(mode), mode === 'pal' ? getPals() : getSavePlayers()])
      .then(([s, subjects]) => {
        if (cancelled) return;
        setSchema(s);
        if (mode === 'pal') setPals(subjects as PalRecord[]);
        else setPlayers(subjects as PlayerSaveData[]);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load the editor');
      });
    return () => { cancelled = true; };
  }, [mode]);

  // Only fields the schema allows AND this Pal actually stores. Rendering an
  // IV the save has no property for produces an input that can only be rejected.
  const editable = useMemo(
    () =>
      (schema?.fields ?? []).filter(
        (f) =>
          !schema?.readOnly.includes(f.name) &&
          // Only offer a field this particular save actually carries.
          (!selected || f.name in draft)
      ),
    [schema, selected, draft]
  );

  // Both subject types collapse to the same shape so one list and one form
  // serve both. Seeds carry only fields the save actually stores — the backend
  // refuses to create an absent property, so offering it would be a dead end.
  const subjects: Subject[] = useMemo(() => {
    if (mode === 'pal') {
      return pals.map((p) => {
        const seed: Record<string, string | number> = {
          nickname: p.nickname ?? '',
          level: p.level ?? 1,
          exp: p.exp ?? 0,
          rank: p.rank ?? 1,
        };
        for (const [iv, value] of Object.entries(p.ivs ?? {})) seed[`ivs.${iv}`] = value;
        return {
          id: p.instanceId,
          title: p.nickname || p.speciesName || p.speciesId,
          subtitle: `Lv ${p.level}${p.nickname && p.speciesName ? ` · ${p.speciesName}` : ''}`,
          seed,
        };
      });
    }
    return players.map((p) => {
      const seed: Record<string, string | number> = {
        nickname: p.name ?? '',
        level: p.level ?? 1,
        exp: p.exp ?? 0,
      };
      const tech = p.progress?.technologyPoints;
      const ancient = p.progress?.ancientTechnologyPoints;
      if (typeof tech === 'number') seed.technologyPoints = tech;
      if (typeof ancient === 'number') seed.ancientTechnologyPoints = ancient;
      return { id: p.uid, title: p.name || p.uid, subtitle: `Lv ${p.level}`, seed };
    });
  }, [mode, pals, players]);

  const matches = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return subjects.slice(0, 40);
    return subjects
      .filter((s) => s.title.toLowerCase().includes(q) || s.subtitle.toLowerCase().includes(q))
      .slice(0, 40);
  }, [subjects, search]);

  const select = useCallback((subject: Subject) => {
    setSelected(subject);
    setPlan(null);
    setError(null);
    setDone(null);
    setDraft({ ...subject.seed });
  }, []);

  const set = (field: string, value: string | number) => {
    setDraft((d) => ({ ...d, [field]: value }));
    setPlan(null);
  };

  /**
   * The game recomputes level from total EXP on load, so changing one without
   * the other is an edit that silently undoes itself. The backend rejects that;
   * this fills in the matching value so it rarely comes up.
   */
  const syncExpToLevel = () => {
    const band = schema?.expBands?.[String(draft.level)];
    if (band) set('exp', band[0]);
  };

  const changes = useMemo(() => {
    if (!selected) return {};
    const out: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(draft)) {
      const field = editable.find((f) => f.name === key);
      if (!field) continue;
      out[key] = field.kind === 'int' ? Number(value) : value;
    }
    return out;
  }, [draft, editable, selected]);

  const preview = async () => {
    if (!selected) return;
    setBusy(true); setError(null); setDone(null);
    try {
      setPlan(
        mode === 'pal'
          ? await previewPalEdit(selected.id, changes)
          : await previewPlayerEdit(selected.id, changes)
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Preview failed');
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!selected || !plan?.ok || !plan.planHash) return;
    if (!confirm(
      `Apply ${plan.changes.length} change(s) to ${selected.title}?\n\n` +
      (plan.touchesPlayerSave && plan.touchesLevelSav
        ? 'This edit spans Level.sav and this player\u2019s own save file.\n\n'
        : '') +
      'A full backup is taken first. The result is read back from disk and verified; ' +
      'if anything does not match, the world is rolled back automatically.'
    )) return;

    setBusy(true); setError(null);
    try {
      const result =
        mode === 'pal'
          ? await applyPalEdit(selected.id, changes, plan.planHash)
          : await applyPlayerEdit(selected.id, changes, plan.planHash);
      const files = result.filesWritten?.length ? ` (${result.filesWritten.join(', ')})` : '';
      setDone(
        `Applied ${result.fieldsChanged} change(s)${files} and verified. ` +
        `Rollback point: ${result.backupId}.`
      );
      setPlan(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Edit failed');
    } finally {
      setBusy(false);
    }
  };

  if (error && !schema) return <div className="notice notice-warn">{error}</div>;
  if (!schema) return <div className="notice">Loading the editor…</div>;

  return (
    <div className="glass-card" style={{ padding: 16 }}>
      <div className="section-title" style={{ marginBottom: 10 }}>
        <PenLine size={14} /> Character editor
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
          {(['pal', 'player'] as Mode[]).map((m) => (
            <button
              key={m}
              className={m === mode ? 'btn btn-primary' : 'btn btn-ghost'}
              style={{ padding: '2px 10px', fontSize: 11 }}
              onClick={() => {
                setMode(m);
                setSelected(null);
                setDraft({});
                setPlan(null);
                setSearch('');
              }}
            >
              {m === 'pal' ? 'Pals' : 'Players'}
            </button>
          ))}
          <span className="badge">Level cap {schema.maxLevel}</span>
        </div>
      </div>

      {!canEdit && (
        <div className="notice notice-warn" style={{ marginBottom: 12 }}>
          <AlertTriangle size={13} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 6 }} />
          The server must be stopped before anything can be written. You can still
          browse and preview.
        </div>
      )}
      {error && <div className="notice notice-warn" style={{ marginBottom: 12 }}>{error}</div>}
      {done && (
        <div className="notice" style={{ marginBottom: 12 }}>
          <ShieldCheck size={13} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 6 }} />
          {done}
        </div>
      )}

      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        {/* ─── Pick a Pal ─── */}
        <div style={{ flex: '1 1 260px', minWidth: 240 }}>
          <div style={{ position: 'relative', marginBottom: 8 }}>
            <Search
              size={13}
              style={{ position: 'absolute', left: 8, top: 9, color: 'var(--text-muted)' }}
            />
            <input
              className="input"
              style={{ paddingLeft: 26, width: '100%' }}
              placeholder={`Search ${subjects.length} ${mode === 'pal' ? 'Pals' : 'players'}…`}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <div style={{
            maxHeight: 320, overflowY: 'auto',
            border: '1px solid var(--border-primary)', borderRadius: 6,
          }}>
            {matches.map((subject) => (
              <button
                key={subject.id}
                onClick={() => select(subject)}
                style={{
                  display: 'block', width: '100%', textAlign: 'left',
                  padding: '7px 10px', fontSize: 12, cursor: 'pointer',
                  background: selected?.id === subject.id ? 'var(--bg-input)' : 'transparent',
                  border: 'none', borderBottom: '1px solid var(--border-primary)',
                  color: 'var(--text-primary)',
                }}
              >
                <span style={{ fontWeight: 500 }}>{subject.title}</span>
                <span style={{ color: 'var(--text-muted)', marginLeft: 6 }}>
                  {subject.subtitle}
                </span>
              </button>
            ))}
            {matches.length === 0 && (
              <p style={{ padding: 12, fontSize: 12, color: 'var(--text-muted)' }}>
                Nothing matches “{search}”.
              </p>
            )}
          </div>
        </div>

        {/* ─── Edit it ─── */}
        <div style={{ flex: '2 1 340px', minWidth: 300 }}>
          {!selected ? (
            <p style={{ fontSize: 13, color: 'var(--text-muted)', padding: '20px 0' }}>
              {mode === 'pal'
                ? 'Pick a Pal to edit. Species, gender and passive skills are not editable — they change what the Pal is, which cascades into the Paldeck and breeding.'
                : 'Pick a player to edit. Name, level and EXP live in Level.sav; technology points live in that player’s own save file, so an edit can touch both.'}
            </p>
          ) : (
            <>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {editable.map((field) => (
                  <div key={field.name}>
                    <label style={{
                      display: 'flex', alignItems: 'center', gap: 6,
                      fontSize: 11, color: 'var(--text-muted)', marginBottom: 3,
                    }}>
                      {field.label}
                      {field.kind === 'int' && field.max != null && (
                        <span>({field.min}–{field.max})</span>
                      )}
                      {field.name === 'exp' && (
                        <button
                          className="btn btn-ghost"
                          style={{ marginLeft: 'auto', padding: '1px 6px', fontSize: 10 }}
                          onClick={syncExpToLevel}
                          title="Set EXP to the minimum for the chosen level. The game recalculates level from EXP on load, so the two must agree."
                        >
                          <Wand2 size={10} /> match level
                        </button>
                      )}
                    </label>
                    <input
                      className="input"
                      style={{ width: '100%' }}
                      type={field.kind === 'int' ? 'number' : 'text'}
                      min={field.min ?? undefined}
                      max={field.max ?? undefined}
                      value={String(draft[field.name] ?? '')}
                      onChange={(e) =>
                        set(field.name, field.kind === 'int' ? e.target.valueAsNumber : e.target.value)
                      }
                    />
                    {field.note && (
                      <p style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>
                        {field.note}
                      </p>
                    )}
                  </div>
                ))}
              </div>

              <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
                <button className="btn" disabled={busy} onClick={preview}>
                  {busy ? 'Working…' : 'Preview changes'}
                </button>
                <button
                  className="btn btn-ghost"
                  disabled={busy}
                  onClick={() => select(selected)}
                  title="Discard edits and reload this Pal's stored values"
                >
                  <Undo2 size={12} /> Reset
                </button>
              </div>

              {plan && <PlanView plan={plan} canEdit={canEdit} busy={busy} onApply={apply} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function PlanView({
  plan, canEdit, busy, onApply,
}: {
  plan: EditPlan; canEdit: boolean; busy: boolean; onApply: () => void;
}) {
  if (!plan.ok) {
    return (
      <div className="notice notice-warn" style={{ marginTop: 12 }}>
        <strong>Cannot apply:</strong>
        <ul style={{ margin: '6px 0 0 16px', fontSize: 12, lineHeight: 1.6 }}>
          {plan.problems.map((p, i) => (
            <li key={i}>{p.field ? <span className="mono">{p.field}</span> : null} {p.problem}</li>
          ))}
        </ul>
      </div>
    );
  }

  if (plan.changes.length === 0) {
    return (
      <div className="notice" style={{ marginTop: 12, fontSize: 12 }}>
        No changes — this Pal already has those values.
      </div>
    );
  }

  return (
    <div style={{
      marginTop: 12, padding: 12,
      border: '1px solid var(--border-primary)', borderRadius: 6,
      background: 'var(--bg-input)',
    }}>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 8 }}>
        {plan.changes.length} change{plan.changes.length === 1 ? '' : 's'} to be written
        {plan.crossFieldChecked === false && ' (cross-field rules not checked)'}
      </div>

      {plan.changes.map((c) => (
        <div key={c.field} style={{
          display: 'flex', justifyContent: 'space-between',
          fontSize: 12, padding: '3px 0',
        }}>
          <span style={{ color: 'var(--text-secondary)' }}>{c.label}</span>
          <span className="mono">
            <span style={{ color: 'var(--text-muted)' }}>{String(c.before)}</span>
            {' → '}
            <span style={{ color: 'var(--accent-emerald)' }}>{String(c.after)}</span>
          </span>
        </div>
      ))}

      <button
        className="btn btn-primary"
        style={{ marginTop: 10 }}
        disabled={!canEdit || busy}
        onClick={onApply}
        title={canEdit ? undefined : 'The server must be stopped first'}
      >
        {busy ? 'Writing…' : 'Apply and verify'}
      </button>
    </div>
  );
}
