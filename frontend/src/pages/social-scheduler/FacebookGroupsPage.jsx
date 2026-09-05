import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { socialSchedulerApi } from '../../api/socialScheduler';
import { getErrorMessage } from '../../api/client';
import { Spinner } from '../../components/Spinner';
import { SocialPageHeader } from '../../components/social/SocialBits';

export default function FacebookGroupsPage() {
  const [groups, setGroups] = useState([]);
  const [form, setForm] = useState({ name: '', url: '' });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await socialSchedulerApi.listShareTargets('facebook');
      setGroups(Array.isArray(data) ? data : []);
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not load your Facebook groups'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const addGroup = async (event) => {
    event.preventDefault();
    const name = form.name.trim();
    const url = form.url.trim();
    if (!name) return toast.error('Enter a group name');
    if (!/^https?:\/\//i.test(url)) return toast.error('Use a full URL starting with https://');
    setBusy(true);
    try {
      const { data } = await socialSchedulerApi.createShareTarget({ name, url });
      setGroups((current) => {
        const exists = current.some((item) => item.id === data.id);
        return exists ? current.map((item) => item.id === data.id ? data : item) : [...current, data];
      });
      setForm({ name: '', url: '' });
      toast.success('Facebook group saved');
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not save the Facebook group'));
    } finally {
      setBusy(false);
    }
  };

  const removeGroup = async (group) => {
    setRemoving(group.id);
    try {
      await socialSchedulerApi.deleteShareTarget(group.id);
      setGroups((current) => current.filter((item) => item.id !== group.id));
      toast.success('Facebook group removed');
    } catch (err) {
      toast.error(getErrorMessage(err, 'Could not remove the Facebook group'));
    } finally {
      setRemoving(null);
    }
  };

  return (
    <div className="mx-auto max-w-4xl">
      <SocialPageHeader
        current="/app/social-scheduler/facebook-groups"
        title="Facebook Groups"
        description="Save group names and links here. They will be available when you upload a post."
      />
      <section className="card p-6">
        <h2 className="text-base font-semibold text-zinc-100">Add a Facebook group</h2>
        <p className="mt-2 text-sm leading-relaxed text-zinc-400">
          Meta no longer supports automatic publishing to Groups. Saved groups appear on the upload page as a manual sharing checklist after the Facebook Page post is published.
        </p>
        <form onSubmit={addGroup} className="mt-5 grid gap-3 sm:grid-cols-[1fr_2fr_auto]">
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Group name" aria-label="Facebook group name" maxLength={120} className="input-field" />
          <input value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="https://www.facebook.com/groups/example" aria-label="Facebook group URL" maxLength={500} className="input-field" />
          <button type="submit" className="btn-primary" disabled={busy}>{busy && <Spinner />}Save group</button>
        </form>
      </section>
      <section className="card mt-5 p-6">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-base font-semibold text-zinc-100">Saved groups</h2>
          <button type="button" onClick={load} className="btn-secondary" disabled={loading}>Refresh</button>
        </div>
        {loading ? <div className="flex h-24 items-center justify-center"><Spinner /></div> : groups.length === 0 ? <p className="mt-5 text-sm text-zinc-500">No groups saved yet.</p> : (
          <ul className="mt-5 divide-y divide-surface-700 rounded-lg border border-surface-700">
            {groups.map((group) => (
              <li key={group.id} className="flex items-center gap-3 px-3 py-3">
                <div className="min-w-0 flex-1"><p className="truncate text-sm font-medium text-zinc-100">{group.name}</p><p className="truncate text-xs text-zinc-500">{group.url}</p></div>
                <a href={group.url} target="_blank" rel="noreferrer" className="text-xs text-accent-400 hover:underline">Open</a>
                <button type="button" onClick={() => removeGroup(group)} disabled={removing === group.id} className="btn-danger">{removing === group.id ? 'Removing…' : 'Remove'}</button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/* Groups remain manual-share targets because Meta removed the Groups API. */
