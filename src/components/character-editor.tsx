'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { PenLine, Search, ShieldCheck, AlertTriangle, Wand2, Undo2, X, Copy } from 'lucide-react';
import {
  getEditSchema, previewPalEdit, applyPalEdit, getPals,
  previewPlayerEdit, applyPlayerEdit, getSavePlayers,
  getPalContainers, previewClone, applyClone, type PalRecord,
} from '@/lib/save-api';
import type {
  EditSchema, EditPlan, PlayerSaveData, PalContainer, ClonePlan,
} from '@/lib/types';
import { buildChanges, type FieldValue as EditFieldValue } from '@/lib/edit-changes';
import { getWorkTypes, type WorkType } from '@/lib/work-types';
import { asArray } from '@/lib/arrays';
import { t } from '@/lib/chrome';

type Mode = 'pal' | 'player';

/**
 * Skill lists are string arrays; everything else is a scalar.
 *
 * `null` is its own thing here rather than "no value": on a `clear` field it is
 * the *request*, meaning "cure this". See `buildChanges`.
 */
type FieldValue = EditFieldValue;

/** Kinds that want a number input and `valueAsNumber` out of it. */
const NUMERIC = new Set(['int', 'float']);

/**
 * How many entries each list field takes.
 *
 * `masteredSkills` is `Infinity` because the learned-move pool has **no cap** —
 * the backend checks only that every entry is a real move. The live world has a
 * Pal with six learned moves against a maximum of three equipped, so borrowing
 * the equipped cap here would refuse real data.
 */
const LIST_MAX: Record<string, number> = {
  activeSkills: 3,
  passiveSkills: 4,
  masteredSkills: Infinity,
  // Ownership history has no cap either — a Pal traded round a server can
  // legitimately carry a long one, and truncating it in the editor would
  // silently discard owners on the next apply.
  previousOwners: Infinity,
};

/** The two subject types share enough shape to drive one editor. */
interface Subject {
  id: string;
  title: string;
  subtitle: string;
  /**
   * Everything this row can be found by, lower-cased.
   *
   * Separate from what is *displayed* because the two want different things: a
   * row shows a friendly name, and a search must also match the internal id the
   * API speaks. Searching the rendered strings meant `SheepBall` found nothing
   * while `Lamball` worked — and the internal id is exactly what someone has in
   * front of them when they arrive here from an export or an error message.
   */
  search: string;
  seed: Record<string, FieldValue>;
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
  // Whose Pals to show. 1,905 Pals in one list is not a chooser.
  const [owner, setOwner] = useState('');
  const [selected, setSelected] = useState<Subject | null>(null);
  const [draft, setDraft] = useState<Record<string, FieldValue>>({});
  const [plan, setPlan] = useState<EditPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  // Players are loaded in both modes: in `player` mode they are the subjects,
  // and in `pal` mode they are how you narrow 1,905 Pals down to one owner's.
  useEffect(() => {
    let cancelled = false;
    Promise.all([getEditSchema(mode), getPals(), getSavePlayers()])
      .then(([s, palList, playerList]) => {
        if (cancelled) return;
        setSchema(s);
        setPals(palList);
        setPlayers(playerList);
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
      asArray(schema?.fields, 'editor schema fields').filter(
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
  /**
   * Owners to offer, from the Pals actually present.
   *
   * Built from `pals` rather than from the player list so an owner uid with no
   * matching player record still appears — those Pals exist and would otherwise
   * be unreachable through the picker.
   */
  const owners = useMemo(() => {
    const counts = new Map<string, number>();
    for (const pal of pals) {
      const uid = (pal.ownerUid || '').toLowerCase();
      if (uid) counts.set(uid, (counts.get(uid) ?? 0) + 1);
    }
    const nameOf = new Map(
      players.map((p) => [(p.uid || '').replace(/-/g, '').toLowerCase(), p.name])
    );
    return [...counts.entries()]
      .map(([uid, count]) => ({
        uid,
        count,
        name: nameOf.get(uid.replace(/-/g, '')) || `${uid.slice(0, 8)}…`,
      }))
      .sort((a, b) => b.count - a.count);
  }, [pals, players]);

  const subjects: Subject[] = useMemo(() => {
    if (mode === 'pal') {
      // Narrowed to one owner when chosen. An admin editing "someone's Pal"
      // knows whose before they know which, and 1,905 rows in one search box
      // makes that the wrong way round.
      const scoped = owner
        ? pals.filter((p) => (p.ownerUid || '').toLowerCase() === owner)
        : pals;
      return scoped.map((p) => {
        const seed: Record<string, FieldValue> = {
          nickname: p.nickname ?? '',
          level: p.level ?? 1,
          exp: p.exp ?? 0,
          rank: p.rank ?? 1,
        };
        for (const [iv, value] of Object.entries(p.ivs ?? {})) seed[`ivs.${iv}`] = value;
        // Skill lists are seeded only when the Pal actually stores them — the
        // backend refuses to create an absent ArrayProperty, so offering an
        // editor for one would be a dead end.
        if (p.passiveSkills) seed.passiveSkills = [...p.passiveSkills];
        if (p.activeSkills) seed.activeSkills = [...p.activeSkills];
        if (p.masteredSkills) seed.masteredSkills = [...p.masteredSkills];
        // Condition, seeded by the same rule and for the same reason: the
        // parser reports `null` for a property the save does not carry, so
        // `!= null` is exactly "this Pal has somewhere to write". A healthy Pal
        // has no `WorkerSick` at all, which is why the three afflictions simply
        // do not appear on one — there is nothing to cure and no field to show.
        if (p.sanity != null) seed.sanity = p.sanity;
        if (p.fullStomach != null) seed.fullStomach = p.fullStomach;
        if (p.workerSick != null) seed.workerSick = p.workerSick;
        if (p.physicalHealth != null) seed.physicalHealth = p.physicalHealth;
        if (p.hungerType != null) seed.hungerType = p.hungerType;
        if (p.skinName != null) seed.skinName = p.skinName;
        if (p.isImported != null) seed.isImported = p.isImported;
        if (p.favoriteIndex != null) seed.favoriteIndex = p.favoriteIndex;
        // `null` here means the Pal has never had a Pal Soul spent on it, so
        // there is no entry to copy a struct shape from — distinct from `{}`,
        // which would mean the property exists and is empty.
        if (p.workRanks != null) seed.workRanks = { ...p.workRanks };
        // Present on 100% of Pals, so unlike the rest of these it is always
        // seeded — the property always exists, only its value type is awkward.
        if (p.previousOwners) seed.previousOwners = [...p.previousOwners];
        // A player usually owns SEVERAL of the same species, often at the same
        // level, and "Lamball · Lv 50" three times over is a list you cannot
        // choose from — you can only guess and check afterwards. The row is
        // keyed on `instanceId` so the selection was always correct; what was
        // missing was any way for a person to see *which* one they had picked.
        //
        // So the subtitle carries what actually differs between two Pals of one
        // species: total IVs, condenser stars, where it is, and finally a short
        // instance id — which is not pretty, but is the only thing guaranteed
        // unique when two Pals are otherwise identical.
        const ivTotal =
          (p.ivs?.hp ?? 0) + (p.ivs?.shot ?? 0) + (p.ivs?.defense ?? 0);
        const marks = [
          `Lv ${p.level}`,
          p.nickname && p.speciesName ? p.speciesName : '',
          ivTotal ? `IV ${ivTotal}` : '',
          (p.rank ?? 1) > 1 ? `${(p.rank ?? 1) - 1}★` : '',
          p.isBoss ? 'alpha' : '',
          p.location === 'base' && p.baseName ? p.baseName : (p.location ?? ''),
          p.instanceId.slice(0, 6),
        ].filter(Boolean);
        return {
          id: p.instanceId,
          title: p.nickname || p.speciesName || p.speciesId,
          subtitle: marks.join(' · '),
          search: [
            p.nickname, p.speciesName, p.speciesId, p.characterId, p.instanceId,
          ].filter(Boolean).join(' ').toLowerCase(),
          seed,
        };
      });
    }
    return players.map((p) => {
      const seed: Record<string, FieldValue> = {
        nickname: p.name ?? '',
        level: p.level ?? 1,
        exp: p.exp ?? 0,
      };
      const tech = p.progress?.technologyPoints;
      const ancient = p.progress?.ancientTechnologyPoints;
      if (typeof tech === 'number') seed.technologyPoints = tech;
      if (typeof ancient === 'number') seed.ancientTechnologyPoints = ancient;
      return {
        id: p.uid,
        title: p.name || p.uid,
        subtitle: `Lv ${p.level}`,
        search: [p.name, p.uid].filter(Boolean).join(' ').toLowerCase(),
        seed,
      };
    });
  }, [mode, pals, players, owner]);

  /**
   * The rows to render, and how many were left out.
   *
   * The cap was **40**, silently. With 1,905 Pals in the world that showed the
   * first 40 and nothing else — so a player's Jetragon was simply absent, while
   * the count beside the list said 559. The count being right is what made it
   * read as missing data rather than as a truncated list.
   *
   * A cap still exists because these are DOM rows and a thousand of them costs
   * real scroll performance, but it is now high enough to hold one player's
   * whole box, and what it hides is **reported** rather than dropped.
   */
  const LIST_CAP = 1000;

  const { matches, hidden } = useMemo(() => {
    const q = search.trim().toLowerCase();
    const all = q
      ? subjects.filter(
          (s) => s.search.includes(q)
        )
      : subjects;
    return { matches: all.slice(0, LIST_CAP), hidden: Math.max(0, all.length - LIST_CAP) };
  }, [subjects, search]);

  const select = useCallback((subject: Subject) => {
    setSelected(subject);
    setPlan(null);
    setError(null);
    setDone(null);
    setDraft({ ...subject.seed });
  }, []);

  const set = (field: string, value: FieldValue) => {
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

  // In `lib/` rather than here so its one non-obvious rule — a `clear` field is
  // omitted unless it reads `null` — is pinned by a test. See `edit-changes.ts`.
  const changes = useMemo(
    () => (selected ? buildChanges(draft, editable) : {}),
    [draft, editable, selected]
  );

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
  if (!schema) return <div className="notice">{t('Loading the editor…')}</div>;

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
          {/* Owner first, then the Pal. Same reasoning as the base inventory
              editor's base-then-container step: the thing an admin knows is
              whose Pal they were asked about, and a single search box over
              1,905 of them makes that the wrong way round. */}
          {mode === 'pal' && owners.length > 1 && (
            <select
              className="select"
              style={{ width: '100%', marginBottom: 8 }}
              value={owner}
              onChange={(e) => { setOwner(e.target.value); setSelected(null); }}
            >
              <option value="">Everyone&apos;s Pals ({pals.length.toLocaleString()})</option>
              {owners.map((o) => (
                <option key={o.uid} value={o.uid}>{o.name} — {o.count.toLocaleString()} Pals</option>
              ))}
            </select>
          )}
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
          {/* What the cap hid. Silently truncating is how a Pal that is right
              there reads as missing. */}
          <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>
            Showing {matches.length.toLocaleString()}
            {hidden > 0
              ? ` of ${(matches.length + hidden).toLocaleString()} — narrow by owner or search to see the rest.`
              : mode === 'pal' && owner
                ? ' — this owner’s Pals.'
                : '.'}
          </p>
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
                      {NUMERIC.has(field.kind) && field.max != null && (
                        <span>({field.min}–{field.max})</span>
                      )}
                      {field.name === 'exp' && (
                        <button
                          className="btn btn-ghost"
                          style={{ marginLeft: 'auto', padding: '1px 6px', fontSize: 10 }}
                          onClick={syncExpToLevel}
                          title={t('Set EXP to the minimum for the chosen level. The game recalculates level from EXP on load, so the two must agree.')}
                        >
                          <Wand2 size={10} /> match level
                        </button>
                      )}
                    </label>
                    {field.kind === 'list' ? (
                      <SkillList
                        values={Array.isArray(draft[field.name]) ? (draft[field.name] as string[]) : []}
                        max={LIST_MAX[field.name] ?? 4}
                        onChange={(next) => set(field.name, next)}
                      />
                    ) : field.kind === 'map' ? (
                      <RankMap
                        values={
                          draft[field.name] && typeof draft[field.name] === 'object'
                            && !Array.isArray(draft[field.name])
                            ? (draft[field.name] as Record<string, number>)
                            : {}
                        }
                        onChange={(next) => set(field.name, next)}
                      />
                    ) : field.kind === 'clear' ? (
                      <Cure
                        current={draft[field.name]}
                        onCure={() => set(field.name, null)}
                        onUndo={() => set(field.name, selected.seed[field.name] ?? '')}
                      />
                    ) : field.kind === 'bool' ? (
                      <label style={{
                        display: 'flex', alignItems: 'center', gap: 6, fontSize: 12,
                        color: 'var(--text-primary)', cursor: 'pointer',
                      }}>
                        <input
                          type="checkbox"
                          checked={Boolean(draft[field.name])}
                          onChange={(e) => set(field.name, e.target.checked)}
                        />
                        {draft[field.name] ? 'Yes' : 'No'}
                      </label>
                    ) : (
                      <input
                        className="input"
                        style={{ width: '100%' }}
                        type={NUMERIC.has(field.kind) ? 'number' : 'text'}
                        // A whole-number step on a float input makes the spinner
                        // round a sanity of 87.5 to 88 with no warning, which is
                        // an edit the operator did not ask for.
                        step={field.kind === 'float' ? 'any' : undefined}
                        min={field.min ?? undefined}
                        max={field.max ?? undefined}
                        value={String(draft[field.name] ?? '')}
                        onChange={(e) =>
                          set(
                            field.name,
                            NUMERIC.has(field.kind) ? e.target.valueAsNumber : e.target.value
                          )
                        }
                      />
                    )}
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

              {/* Keyed by the Pal: selecting a different one must remount this
                  rather than carry a plan built for the previous Pal across. */}
              {mode === 'pal' && (
                <ClonePanel
                  key={selected.id}
                  subjectId={selected.id}
                  title={selected.title}
                  canEdit={canEdit}
                />
              )}
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
        <strong>{t('Cannot apply:')}</strong>
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

/**
 * Bought work ranks, as `{workType: rank}` rows.
 *
 * The work types come from `lib/work-types.ts`, which reads the bundled table —
 * the same 13 the backend validates against, so the dropdown cannot offer
 * something the writer will reject.
 *
 * **No maximum on the rank**, matching the schema. Six is the highest observed
 * across three real worlds and the game ships no table carrying a cap, so a
 * `max` here would be this file inventing one.
 */
function RankMap({
  values, onChange,
}: {
  values: Record<string, number>;
  onChange: (next: Record<string, number>) => void;
}) {
  const [types, setTypes] = useState<WorkType[]>([]);
  useEffect(() => {
    let live = true;
    // Falls back to the bundled list inside `getWorkTypes` if the fetch fails,
    // so an offline moment degrades to raw ids rather than an empty dropdown.
    getWorkTypes().then((t) => { if (live) setTypes(t); }).catch(() => undefined);
    return () => { live = false; };
  }, []);
  const unused = types.filter((t) => !(t.id in values));

  const setRank = (id: string, rank: number) => {
    onChange({ ...values, [id]: Number.isFinite(rank) ? rank : 1 });
  };
  const remove = (id: string) => {
    const next = { ...values };
    delete next[id];
    onChange(next);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      {Object.entries(values).map(([id, rank]) => (
        <div key={id} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 12, minWidth: 150 }}>
            {types.find((t) => t.id === id)?.label ?? id}
          </span>
          <input
            className="input"
            type="number"
            min={1}
            style={{ width: 70 }}
            value={rank}
            onChange={(e) => setRank(id, e.target.valueAsNumber)}
          />
          <button
            className="btn btn-ghost"
            style={{ padding: '1px 6px', fontSize: 10 }}
            onClick={() => remove(id)}
            title={t('Removing a work type deletes its bought rank')}
          >
            <X size={10} />
          </button>
        </div>
      ))}
      {unused.length > 0 && (
        <select
          className="input"
          style={{ fontSize: 12 }}
          value=""
          onChange={(e) => e.target.value && setRank(e.target.value, 1)}
        >
          <option value="">{t('Add a work type…')}</option>
          {unused.map((t) => (
            <option key={t.id} value={t.id}>{t.label}</option>
          ))}
        </select>
      )}
      {Object.keys(values).length === 0 && (
        <p style={{ fontSize: 10, color: 'var(--text-muted)' }}>
          None — applying this removes every bought rank from this Pal.
        </p>
      )}
    </div>
  );
}

/**
 * An affliction, and the one action available on it.
 *
 * There is no input here because there is no value to type. A healthy Pal does
 * not carry `WorkerSick` at all, so the save has no "well" state to write —
 * curing is a deletion, and `null` is the only value the backend accepts. A
 * text box would invite someone to type "Healthy" and be told it is invalid.
 *
 * The same asymmetry is why nothing here can *inflict* one: this is a dashboard
 * for fixing a base, and there is no verified value to write even if it were
 * wanted.
 */
function Cure({
  current, onCure, onUndo,
}: {
  current: FieldValue;
  onCure: () => void;
  onUndo: () => void;
}) {
  const cured = current === null;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{
        fontSize: 12,
        color: cured ? 'var(--text-muted)' : 'var(--accent-danger, #e5484d)',
        textDecoration: cured ? 'line-through' : 'none',
      }}>
        {String(current ?? '')}
      </span>
      {cured ? (
        <>
          <span style={{ fontSize: 11, color: 'var(--accent-success, #30a46c)' }}>
            → will be cured
          </span>
          <button
            className="btn btn-ghost"
            style={{ padding: '1px 6px', fontSize: 10 }}
            onClick={onUndo}
          >
            <Undo2 size={10} /> keep it
          </button>
        </>
      ) : (
        <button
          className="btn btn-ghost"
          style={{ padding: '1px 8px', fontSize: 11 }}
          onClick={onCure}
        >
          Cure
        </button>
      )}
    </div>
  );
}

/**
 * A skill list, as removable chips plus a free-text add box.
 *
 * Deliberately free text rather than a dropdown of all 375 moves and 1,905
 * passives: the backend validates against the bundled tables and returns a
 * readable rejection naming the bad id, so a wrong entry costs one preview.
 * Shipping the whole catalogue to the browser to prevent that is not worth it.
 */
function SkillList({
  values, max, onChange,
}: {
  values: string[];
  max: number;
  onChange: (next: string[]) => void;
}) {
  const [entry, setEntry] = useState('');

  const add = () => {
    const id = entry.trim();
    if (!id || values.includes(id) || values.length >= max) return;
    onChange([...values, id]);
    setEntry('');
  };

  return (
    <div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
        {values.map((id) => (
          <span
            key={id}
            className="mono"
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 4,
              fontSize: 11, padding: '2px 6px', borderRadius: 4,
              background: 'var(--bg-input)', border: '1px solid var(--border-primary)',
            }}
          >
            {id}
            <button
              onClick={() => onChange(values.filter((v) => v !== id))}
              style={{
                background: 'none', border: 'none', cursor: 'pointer', padding: 0,
                display: 'flex', color: 'var(--text-muted)',
              }}
              title={`Remove ${id}`}
            >
              <X size={10} />
            </button>
          </span>
        ))}
        {values.length === 0 && (
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>none</span>
        )}
      </div>

      <div style={{ display: 'flex', gap: 6 }}>
        <input
          className="input"
          style={{ flex: 1, fontSize: 12, padding: '3px 6px' }}
          placeholder={values.length >= max ? `${max} is the maximum` : 'Add by id…'}
          value={entry}
          disabled={values.length >= max}
          onChange={(e) => setEntry(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); add(); }
          }}
        />
        <button
          className="btn btn-ghost"
          style={{ padding: '2px 10px', fontSize: 11 }}
          disabled={!entry.trim() || values.length >= max}
          onClick={add}
        >
          Add
        </button>
      </div>
    </div>
  );
}

/**
 * Duplicate the selected Pal into a chosen container.
 *
 * The only operation in the dashboard that *creates* save records, so it is
 * kept visually separate from the field editor above and states plainly where
 * the copies will land. There is no "put it anywhere with room" option: silently
 * dropping Pals into someone else's palbox is worse than an error.
 */
function ClonePanel({
  subjectId, title, canEdit,
}: {
  subjectId: string;
  title: string;
  canEdit: boolean;
}) {
  const [containers, setContainers] = useState<PalContainer[]>([]);
  const [containerId, setContainerId] = useState('');
  const [count, setCount] = useState(1);
  const [plan, setPlan] = useState<ClonePlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open || containers.length) return;
    getPalContainers()
      .then((r) => setContainers(r.containers))
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : 'Could not list containers')
      );
  }, [open, containers.length]);

  const preview = async () => {
    setBusy(true); setError(null); setDone(null);
    try {
      setPlan(await previewClone(subjectId, containerId, count));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Preview failed');
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!plan?.ok || !plan.planHash) return;
    if (!confirm(
      `Create ${count} copy/copies of ${title}?\n\n` +
      'This adds new Pals to the world rather than changing existing ones. A full ' +
      'backup is taken first, and afterwards the save is re-read and checked: both ' +
      'the character list and the target container must have grown by exactly this ' +
      'many, and no other container may have changed. Anything else rolls back.'
    )) return;

    setBusy(true); setError(null);
    try {
      const result = await applyClone(subjectId, containerId, count, plan.planHash);
      setDone(
        `Created ${result.count} copy/copies in slots ${result.slotIndices.join(', ')} ` +
        `and verified. Rollback point: ${result.backupId}.`
      );
      setPlan(null);
      setContainers([]);   // capacities moved
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Clone failed');
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button
        className="btn btn-ghost"
        style={{ marginTop: 12, fontSize: 11, padding: '3px 10px' }}
        onClick={() => setOpen(true)}
      >
        <Copy size={12} /> Duplicate this Pal
      </button>
    );
  }

  return (
    <div style={{
      marginTop: 12, padding: 12,
      border: '1px solid var(--border-primary)', borderRadius: 6,
    }}>
      <div className="section-title" style={{ marginBottom: 8, fontSize: 12 }}>
        <Copy size={12} /> Duplicate {title}
        <button
          className="btn btn-ghost"
          style={{ marginLeft: 'auto', padding: '1px 8px', fontSize: 10 }}
          onClick={() => { setOpen(false); setPlan(null); }}
        >
          Close
        </button>
      </div>

      {error && <div className="notice notice-warn" style={{ marginBottom: 8 }}>{error}</div>}
      {done && (
        <div className="notice" style={{ marginBottom: 8 }}>
          <ShieldCheck size={12} style={{ display: 'inline', verticalAlign: '-2px', marginRight: 5 }} />
          {done}
        </div>
      )}

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <div style={{ flex: '1 1 260px' }}>
          <label style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', marginBottom: 3 }}>
            Destination
          </label>
          <select
            className="input"
            style={{ width: '100%' }}
            value={containerId}
            onChange={(e) => { setContainerId(e.target.value); setPlan(null); }}
          >
            <option value="">{t('Pick a container…')}</option>
            {containers.map((c) => (
              <option key={c.containerId} value={c.containerId} disabled={c.free === 0}>
                {c.containerId.slice(0, 8)}… — {c.used}/{c.capacity} used, {c.free} free
              </option>
            ))}
          </select>
        </div>
        <div style={{ width: 90 }}>
          <label style={{ display: 'block', fontSize: 11, color: 'var(--text-muted)', marginBottom: 3 }}>
            How many
          </label>
          <input
            className="input"
            type="number"
            min={1}
            max={50}
            style={{ width: '100%' }}
            value={count}
            onChange={(e) => { setCount(e.target.valueAsNumber || 1); setPlan(null); }}
          />
        </div>
        <button
          className="btn"
          disabled={busy || !containerId}
          onClick={preview}
          title={!containerId ? 'Pick a destination first' : undefined}
        >
          {busy ? 'Working…' : 'Preview'}
        </button>
      </div>

      {plan && (
        <div style={{ marginTop: 10 }}>
          {!plan.ok ? (
            <div className="notice notice-warn">
              {plan.problems.map((p, i) => <div key={i}>{p.problem}</div>)}
            </div>
          ) : (
            <div style={{
              padding: 10, borderRadius: 6, background: 'var(--bg-input)',
              border: '1px solid var(--border-primary)',
            }}>
              <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                {plan.count} copy/copies into slot{plan.slotIndices!.length === 1 ? '' : 's'}{' '}
                <span className="mono">{plan.slotIndices!.join(', ')}</span>, leaving{' '}
                {plan.freeAfter} free of {plan.capacity}.
              </div>
              <button
                className="btn btn-primary"
                style={{ marginTop: 8 }}
                disabled={!canEdit || busy}
                onClick={apply}
                title={!canEdit ? 'The server must be stopped first' : undefined}
              >
                {busy ? 'Writing…' : `Create ${plan.count}`}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
