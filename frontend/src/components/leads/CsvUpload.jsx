import { useCallback, useRef, useState } from 'react';
import Papa from 'papaparse';
import toast from 'react-hot-toast';
import { leadsApi } from '../../api/endpoints';
import { getErrorMessage } from '../../api/client';
import { Spinner } from '../Spinner';

const REQUIRED_HEADERS = ['first_name', 'last_name', 'linkedin_url'];
const PREVIEW_ROWS = 10;

/**
 * Drag-and-drop CSV upload with client-side preview (Papaparse) before
 * sending. The backend validates every row before inserting any, and
 * returns 422 { message, errors: [...] } — the per-row errors are rendered
 * as an explicit scrollable list.
 */
export default function CsvUpload({ campaignId, ownerEmail, onUploaded }) {
  const [dragging, setDragging] = useState(false);
  const [file, setFile] = useState(null);
  const [headers, setHeaders] = useState([]);
  const [preview, setPreview] = useState([]);
  const [rowCount, setRowCount] = useState(0);
  const [headerError, setHeaderError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [rowErrors, setRowErrors] = useState(null);
  const [rowErrorsMessage, setRowErrorsMessage] = useState('');
  const inputRef = useRef(null);

  const reset = useCallback(() => {
    setFile(null);
    setHeaders([]);
    setPreview([]);
    setRowCount(0);
    setHeaderError(null);
    setRowErrors(null);
    setRowErrorsMessage('');
    if (inputRef.current) inputRef.current.value = '';
  }, []);

  const parseFile = useCallback((f) => {
    setHeaderError(null);
    setRowErrors(null);
    setRowErrorsMessage('');
    Papa.parse(f, {
      preview: PREVIEW_ROWS,
      skipEmptyLines: true,
      complete: (result) => {
        const rows = result.data || [];
        const cols = (rows[0] || []).map((h) => String(h).trim().toLowerCase());
        const missing = REQUIRED_HEADERS.filter((h) => !cols.includes(h));

        setHeaders(rows[0] || []);
        setPreview(rows.slice(1));

        // Count total rows with a second full parse (header + data).
        Papa.parse(f, {
          skipEmptyLines: true,
          complete: (full) => setRowCount(Math.max(0, (full.data || []).length - 1)),
        });

        if (missing.length > 0) {
          setHeaderError(
            `Missing required header${missing.length > 1 ? 's' : ''}: ${missing.join(', ')}. ` +
              `Found: ${cols.join(', ') || '(none)'}.`
          );
        }
        setFile(f);
      },
      error: () => {
        setHeaderError('Could not parse this file — is it a valid CSV?');
        setFile(null);
      },
    });
  }, []);

  function onDrop(e) {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files?.[0];
    if (f) {
      if (!f.name.toLowerCase().endsWith('.csv')) {
        toast.error('Only .csv files are supported.');
        return;
      }
      parseFile(f);
    }
  }

  async function upload() {
    if (!file || headerError) return;
    setBusy(true);
    setRowErrors(null);
    try {
      const { data } = await leadsApi.uploadCsv(file, campaignId, ownerEmail);
      // Backend returns the array of created leads on success.
      const imported = Array.isArray(data) ? data.length : (data?.imported ?? rowCount);
      toast.success(`Imported ${imported} lead${imported === 1 ? '' : 's'} — all rows passed validation.`);
      reset();
      onUploaded?.();
    } catch (err) {
      const detail = err?.response?.data?.detail;
      if (err?.response?.status === 422 && detail && typeof detail === 'object' && !Array.isArray(detail)) {
        // Row-level validation failure — nothing was imported.
        setRowErrorsMessage(detail.message || 'CSV contains invalid rows; no leads were imported.');
        setRowErrors(detail.errors || []);
      } else if (err?.response?.status === 422 && Array.isArray(detail)) {
        setRowErrorsMessage('CSV failed validation.');
        setRowErrors(detail.map((d) => d.msg || JSON.stringify(d)));
      } else {
        toast.error(getErrorMessage(err, 'Upload failed.'));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      {/* Dropzone */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => e.key === 'Enter' && inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-10 text-center transition ${
          dragging
            ? 'border-accent-400 bg-accent-500/10'
            : 'border-surface-600 bg-surface-800/50 hover:border-zinc-500 hover:bg-surface-800'
        }`}
      >
        <svg className="mb-3 h-9 w-9 text-zinc-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 16.5V9.75m0 0 3 3m-3-3-3 3M6.75 19.5a4.5 4.5 0 0 1-1.41-8.775 5.25 5.25 0 0 1 10.233-2.33 3 3 0 0 1 3.758 3.848A3.752 3.752 0 0 1 18 19.5H6.75Z" />
        </svg>
        <p className="text-sm font-medium text-zinc-200">
          {file ? file.name : 'Drop your CSV here, or click to browse'}
        </p>
        <p className="mt-1 text-xs text-zinc-500">
          Required headers: <code className="rounded bg-surface-700 px-1 py-0.5 text-[11px] text-accent-300">first_name, last_name, linkedin_url</code>
          {' '}(<code className="rounded bg-surface-700 px-1 py-0.5 text-[11px] text-zinc-400">headline</code> optional)
        </p>
        <input
          ref={inputRef}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && parseFile(e.target.files[0])}
        />
      </div>

      {/* Header error */}
      {headerError && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          <p className="font-medium">Header problem</p>
          <p className="mt-0.5">{headerError}</p>
        </div>
      )}

      {/* Client-side preview of first rows */}
      {file && !headerError && preview.length > 0 && (
        <div className="rounded-lg border border-surface-700">
          <div className="flex items-center justify-between border-b border-surface-700 bg-surface-800 px-4 py-2">
            <p className="text-xs font-medium uppercase tracking-wide text-zinc-400">
              Preview — first {Math.min(preview.length, PREVIEW_ROWS - 1)} row{preview.length === 1 ? '' : 's'}
            </p>
            <p className="text-xs text-zinc-500">{rowCount} total row{rowCount === 1 ? '' : 's'} detected</p>
          </div>
          <div className="scrollbar-thin max-h-56 overflow-auto">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-surface-850">
                <tr>
                  <th className="px-4 py-2 font-medium text-zinc-500">#</th>
                  {headers.map((h, i) => (
                    <th key={i} className="px-4 py-2 font-medium text-zinc-500">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-700">
                {preview.map((row, r) => (
                  <tr key={r} className="text-zinc-300">
                    <td className="px-4 py-1.5 text-zinc-600">{r + 1}</td>
                    {row.map((cell, c) => (
                      <td key={c} className="max-w-[220px] truncate px-4 py-1.5">{cell}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Server-side row errors: every bad row listed explicitly */}
      {rowErrors && (
        <div className="rounded-lg border border-red-500/50 bg-red-500/10">
          <div className="border-b border-red-500/30 px-4 py-3">
            <p className="text-sm font-semibold text-red-300">{rowErrorsMessage}</p>
            <p className="mt-0.5 text-xs text-red-300/80">
              Nothing was imported — fix {rowErrors.length} row{rowErrors.length === 1 ? '' : 's'} and
              upload again.
            </p>
          </div>
          <ul className="scrollbar-thin max-h-52 list-none divide-y divide-red-500/15 overflow-auto px-4">
            {rowErrors.map((msg, i) => (
              <li key={i} className="flex items-start gap-2 py-2 text-xs text-red-200">
                <svg className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-400" viewBox="0 0 20 20" fill="currentColor">
                  <path fillRule="evenodd" d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16ZM8.28 7.22a.75.75 0 0 0-1.06 1.06L8.94 10l-1.72 1.72a.75.75 0 1 0 1.06 1.06L10 11.06l1.72 1.72a.75.75 0 1 0 1.06-1.06L11.06 10l1.72-1.72a.75.75 0 0 0-1.06-1.06L10 8.94 8.28 7.22Z" clipRule="evenodd" />
                </svg>
                <span className="break-all">{msg}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex items-center gap-3">
        <button onClick={upload} className="btn-primary" disabled={!file || Boolean(headerError) || busy}>
          {busy && <Spinner />}
          {busy ? 'Uploading…' : `Upload ${rowCount > 0 ? `${rowCount} lead${rowCount === 1 ? '' : 's'}` : 'CSV'}`}
        </button>
        {file && (
          <button onClick={reset} className="btn-secondary" disabled={busy}>
            Clear
          </button>
        )}
      </div>
    </div>
  );
}
