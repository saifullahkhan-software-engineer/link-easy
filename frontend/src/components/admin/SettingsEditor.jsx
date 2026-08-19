import { useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { adminApi } from '../../api/endpoints';
import { getErrorMessage } from '../../api/client';
import { Spinner } from '../Spinner';

/**
 * Admin settings editor — reused by the LinkedIn and WhatsApp admin pages.
 *
 * Fetches the full settings registry once, shows only the requested
 * categories, and saves changes through PUT /admin/settings. Values are
 * validated + clamped on the backend; the UI surfaces backend errors as-is.
 */
export default function SettingsEditor({ categories, title, description }) {
  const [settings, setSettings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState({});
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      const { data } = await adminApi.getSettings();
      setSettings(data?.settings || []);
      setDraft({});
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not load settings'), { id: 'admin-settings-load' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const visible = useMemo(
    () => settings.filter((s) => categories.includes(s.category)),
    [settings, categories],
  );

  const grouped = useMemo(() => {
    const out = {};
    visible.forEach((s) => {
      (out[s.category] = out[s.category] || []).push(s);
    });
    return out;
  }, [visible]);

  const save = async () => {
    const values = {};
    Object.entries(draft).forEach(([key, raw]) => {
      const spec = settings.find((s) => s.key === key);
      if (!spec) return;
      const parsed = spec.value_type === 'float' ? parseFloat(raw) : parseInt(raw, 10);
      if (!Number.isNaN(parsed)) values[key] = parsed;
    });
    if (!Object.keys(values).length) {
      toast('Nothing changed');
      return;
    }
    setSaving(true);
    try {
      const { data } = await adminApi.updateSettings(values);
      setSettings(data?.settings || []);
      setDraft({});
      toast.success('Settings saved');
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not save settings'));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <section className="rounded-2xl border border-surface-700 bg-surface-900/60 p-5">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-zinc-100">{title}</h2>
          {description ? <p className="mt-1 text-sm text-zinc-500">{description}</p> : null}
        </div>
        <button
          type="button"
          onClick={save}
          disabled={saving || !Object.keys(draft).length}
          className="btn-primary px-4 py-2 text-sm disabled:opacity-50"
          data-testid="save-settings"
        >
          {saving ? 'Saving…' : 'Save changes'}
        </button>
      </div>

      {!visible.length ? (
        <p className="text-sm text-zinc-500">No settings in this category.</p>
      ) : (
        <div className="space-y-6">
          {Object.entries(grouped).map(([category, rows]) => (
            <div key={category}>
              <h3 className="mb-3 text-xs font-semibold uppercase tracking-[0.18em] text-zinc-500">
                {category}
              </h3>
              <div className="grid gap-3 md:grid-cols-2">
                {rows.map((s) => (
                  <label key={s.key} className="rounded-xl border border-surface-700 bg-surface-900 p-3">
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-sm font-medium text-zinc-200">
                        {s.key.split('.').slice(1).join('.')}
                      </span>
                      <input
                        type="number"
                        step={s.value_type === 'float' ? '0.5' : '1'}
                        min={s.minimum ?? undefined}
                        max={s.maximum ?? undefined}
                        value={draft[s.key] ?? s.value}
                        onChange={(e) => setDraft((d) => ({ ...d, [s.key]: e.target.value }))}
                        className="w-28 rounded-lg border border-surface-700 bg-surface-950 px-2 py-1.5 text-right text-sm text-zinc-100"
                      />
                    </div>
                    <p className="mt-1.5 text-xs leading-5 text-zinc-500">{s.description}</p>
                    {s.maximum != null && (
                      <p className="mt-0.5 text-[11px] text-zinc-600">
                        max {s.maximum} · default {s.default}
                      </p>
                    )}
                  </label>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
