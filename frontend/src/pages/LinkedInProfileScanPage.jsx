/**
 * Preview-first LinkedIn profile scanner.
 *
 * A scan returns structured profile data and the PDF generated from that exact
 * data. The report and embedded PDF are rendered first; only then is the
 * download action mounted.
 */
import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { linkedinProfileApi } from '../api/endpoints';
import { getErrorMessage } from '../api/client';
import { Spinner } from '../components/Spinner';

function isLinkedInProfileUrl(value) {
  try {
    const parsed = new URL(value);
    return (
      parsed.protocol === 'https:' &&
      ['linkedin.com', 'www.linkedin.com'].includes(parsed.hostname.toLowerCase()) &&
      parsed.pathname.startsWith('/in/')
    );
  } catch {
    return false;
  }
}

function pdfBlobFromBase64(value) {
  const binary = window.atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return new Blob([bytes], { type: 'application/pdf' });
}

export default function LinkedInProfileScanPage() {
  const [profileUrl, setProfileUrl] = useState('');
  const [scanning, setScanning] = useState(false);
  const [report, setReport] = useState(null);
  const [pdfUrl, setPdfUrl] = useState(null);
  const [filename, setFilename] = useState('linkedin-profile-scan.pdf');

  useEffect(() => () => {
    if (pdfUrl) URL.revokeObjectURL(pdfUrl);
  }, [pdfUrl]);

  const clearResult = () => {
    setReport(null);
    setPdfUrl((current) => {
      if (current) URL.revokeObjectURL(current);
      return null;
    });
  };

  const handleScan = async (event) => {
    event.preventDefault();
    const input = profileUrl.trim();
    if (!isLinkedInProfileUrl(input)) {
      toast.error('Enter a full LinkedIn profile URL such as https://www.linkedin.com/in/name.');
      return;
    }

    clearResult();
    setScanning(true);
    try {
      const { data } = await linkedinProfileApi.scan(input);
      if (!data?.report || !data?.pdf_base64) {
        throw new Error('The scan completed without preview data.');
      }
      const blob = pdfBlobFromBase64(data.pdf_base64);
      const objectUrl = URL.createObjectURL(blob);
      setReport(data.report);
      setPdfUrl(objectUrl);
      setFilename(data.filename || 'linkedin-profile-scan.pdf');
      toast.success('Profile scanned. Review the preview before downloading.');
    } catch (error) {
      clearResult();
      toast.error(
        error?.response
          ? getErrorMessage(error, 'Profile scan failed.')
          : error?.message || 'Profile scan failed.',
      );
    } finally {
      setScanning(false);
    }
  };

  const basics = report?.basics || {};

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 lg:px-8">
      <header>
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-sky-400">
          LinkedIn intelligence
        </p>
        <h1 className="mt-2 text-2xl font-semibold text-zinc-100">Profile Scan & PDF</h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-zinc-400">
          Scan a profile with your connected LinkedIn session, inspect the extracted report and
          PDF preview, then download the reviewed document.
        </p>
      </header>

      <form onSubmit={handleScan} className="card mt-6 p-5" data-testid="linkedin-profile-form">
        <label htmlFor="linkedin-profile-url" className="block text-sm font-medium text-zinc-200">
          LinkedIn profile URL
        </label>
        <div className="mt-2 flex flex-col gap-3 sm:flex-row">
          <input
            id="linkedin-profile-url"
            type="url"
            value={profileUrl}
            onChange={(event) => {
              setProfileUrl(event.target.value);
              if (report) clearResult();
            }}
            placeholder="https://www.linkedin.com/in/person-name"
            className="min-w-0 flex-1 rounded-lg border border-surface-700 bg-surface-950 px-3.5 py-2.5 text-sm text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-sky-500"
            disabled={scanning}
            required
            data-testid="linkedin-profile-url"
          />
          <button
            type="submit"
            disabled={scanning || !profileUrl.trim()}
            className="btn-primary inline-flex min-w-36 items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
            data-testid="linkedin-profile-scan"
          >
            {scanning ? <Spinner className="h-4 w-4" /> : null}
            {scanning ? 'Scanning…' : 'Scan profile'}
          </button>
        </div>
        <p className="mt-3 text-xs text-zinc-500">
          If live chat is stopped, a temporary LinkedIn browser starts automatically and closes
          after the scan. An active chat is restored to the same conversation.
        </p>
      </form>

      {scanning ? (
        <div className="card mt-6 flex items-center gap-4 p-6" data-testid="linkedin-profile-loading">
          <Spinner className="h-6 w-6 text-sky-400" />
          <div>
            <p className="text-sm font-medium text-zinc-200">Reading the profile…</p>
            <p className="mt-1 text-xs text-zinc-500">
              LinkedIn loads sections progressively; this can take up to a minute.
            </p>
          </div>
        </div>
      ) : null}

      {report && pdfUrl ? (
        <section className="mt-6 space-y-5" data-testid="linkedin-profile-preview">
          <div className="card overflow-hidden p-0">
            <div className="border-b border-surface-700 bg-gradient-to-r from-sky-500/10 to-transparent px-5 py-5">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <p className="text-xs font-medium uppercase tracking-wider text-sky-400">
                    Scan preview
                  </p>
                  <h2 className="mt-1 text-xl font-semibold text-zinc-100">
                    {basics.name || 'LinkedIn profile'}
                  </h2>
                  {basics.headline ? (
                    <p className="mt-1 max-w-3xl text-sm text-zinc-300">{basics.headline}</p>
                  ) : null}
                  {basics.current_position ? (
                    <p className="mt-2 text-xs text-zinc-400">{basics.current_position}</p>
                  ) : null}
                  {basics.location ? (
                    <p className="mt-1 text-xs text-zinc-500">{basics.location}</p>
                  ) : null}
                </div>
                <span className="rounded-full bg-emerald-500/10 px-3 py-1 text-xs font-medium text-emerald-300 ring-1 ring-emerald-500/20">
                  Ready to review
                </span>
              </div>
            </div>

            <div className="grid gap-5 p-5 lg:grid-cols-2">
              <PreviewSection title="About" empty="No About section was visible.">
                {report.about ? <p className="whitespace-pre-wrap">{report.about}</p> : null}
              </PreviewSection>

              <PreviewSection title="Skills" empty="No skills were visible.">
                {report.skills?.length ? (
                  <div className="flex flex-wrap gap-2">
                    {report.skills.map((skill) => (
                      <span key={skill} className="rounded-md bg-surface-700 px-2.5 py-1 text-xs text-zinc-300">
                        {skill}
                      </span>
                    ))}
                  </div>
                ) : null}
              </PreviewSection>

              <PreviewSection title="Experience" empty="No experience entries were visible.">
                {report.experience?.length ? (
                  <EntryList
                    entries={report.experience}
                    primary="title"
                    secondary="company"
                  />
                ) : null}
              </PreviewSection>

              <PreviewSection title="Education" empty="No education entries were visible.">
                {report.education?.length ? (
                  <EntryList
                    entries={report.education}
                    primary="school"
                    secondary="degree"
                  />
                ) : null}
              </PreviewSection>
            </div>
          </div>

          <div className="card p-5">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold text-zinc-100">Generated PDF preview</h2>
                <p className="mt-1 text-xs text-zinc-500">
                  This is the exact document that will be downloaded.
                </p>
              </div>
            </div>
            <iframe
              src={pdfUrl}
              title="LinkedIn profile PDF preview"
              className="h-[620px] w-full rounded-lg border border-surface-700 bg-white"
              data-testid="linkedin-profile-pdf-preview"
            />
          </div>

          {/* The download action only exists after both report and PDF previews render. */}
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-4">
            <p className="text-sm text-zinc-300">Preview complete. Download the reviewed PDF when ready.</p>
            <a
              href={pdfUrl}
              download={filename}
              className="btn-primary inline-flex items-center gap-2"
              data-testid="linkedin-profile-download"
            >
              <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                <path d="M10.75 2.75a.75.75 0 0 0-1.5 0v8.69L6.53 8.72a.75.75 0 0 0-1.06 1.06l4 4a.75.75 0 0 0 1.06 0l4-4a.75.75 0 1 0-1.06-1.06l-2.72 2.72V2.75Z" />
                <path d="M3.5 12.5a.75.75 0 0 0-1.5 0v2.75A2.75 2.75 0 0 0 4.75 18h10.5A2.75 2.75 0 0 0 18 15.25V12.5a.75.75 0 0 0-1.5 0v2.75c0 .69-.56 1.25-1.25 1.25H4.75c-.69 0-1.25-.56-1.25-1.25V12.5Z" />
              </svg>
              Download PDF
            </a>
          </div>
        </section>
      ) : null}
    </div>
  );
}

function PreviewSection({ title, empty, children }) {
  const hasContent = Boolean(children);
  return (
    <div className="rounded-xl border border-surface-700 bg-surface-850 p-4">
      <h3 className="text-sm font-semibold text-zinc-200">{title}</h3>
      <div className="mt-3 text-sm leading-6 text-zinc-400">
        {hasContent ? children : <p className="italic text-zinc-600">{empty}</p>}
      </div>
    </div>
  );
}

function EntryList({ entries = [], primary, secondary }) {
  if (!entries.length) return null;
  return (
    <ul className="space-y-3">
      {entries.map((entry, index) => (
        <li key={`${entry[primary] || 'entry'}-${index}`} className="border-l-2 border-sky-500/30 pl-3">
          <p className="font-medium text-zinc-200">{entry[primary] || 'Untitled'}</p>
          {entry[secondary] ? <p className="text-xs text-zinc-400">{entry[secondary]}</p> : null}
          {entry.dates ? <p className="mt-1 text-xs text-zinc-600">{entry.dates}</p> : null}
          {entry.location ? <p className="text-xs text-zinc-600">{entry.location}</p> : null}
        </li>
      ))}
    </ul>
  );
}
