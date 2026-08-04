// DomainListInput.jsx — a scope-list field used wherever the operator enters a list of
// (sub)domains: a textarea plus an "Upload list" button that ingests a .txt/.csv/.json file
// (or paste) in any supported format and merges the parsed hosts in, de-duped. Controlled
// via value/onChange (a newline-joined string); callers parse the final value with
// parseDomainList on submit. Shows a live parsed-count so the operator sees what will save.
import { useRef } from 'preact/hooks';
import { C, F } from '../theme.js';
import { parseDomainList, readFileText } from '../scopeImport.js';
import { DownloadIcon } from '../icons.jsx';

export function DomainListInput({ value, onChange, rows = 4, placeholder }) {
  const fileRef = useRef(null);
  const parsed = parseDomainList(value);

  const onFile = async (e) => {
    const file = e.target.files && e.target.files[0];
    e.target.value = '';   // let the same file be picked again later
    if (!file) return;
    try {
      const text = await readFileText(file);
      // Merge whatever's already typed with the file's hosts, cleaned + de-duped, into a
      // tidy one-per-line list.
      const merged = parseDomainList(`${value || ''}\n${parseDomainList(text).join('\n')}`);
      onChange(merged.join('\n'));
    } catch (err) {
      /* unreadable file — leave the field as-is */
    }
  };

  return (
    <div>
      <textarea
        value={value}
        onInput={(e) => onChange(e.target.value)}
        rows={rows}
        placeholder={placeholder || 'app.target.com\napi.target.com'}
        style={{
          width: '100%', boxSizing: 'border-box', background: C.inset, border: `1px solid ${C.lineStrong}`,
          borderRadius: '10px', color: C.text, fontFamily: F.mono, fontSize: '13px', padding: '11px 13px',
          outline: 'none', resize: 'vertical'
        }}
      />
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginTop: '7px' }}>
        <button
          type="button"
          onClick={() => fileRef.current && fileRef.current.click()}
          style={{
            display: 'flex', alignItems: 'center', gap: '6px', padding: '6px 11px', borderRadius: '8px',
            border: `1px solid ${C.lineHover}`, background: C.control, color: C.textSoft, cursor: 'pointer',
            fontSize: '11.5px', fontWeight: 600
          }}
        >
          <DownloadIcon size={12} />Upload list…
        </button>
        <input
          ref={fileRef} type="file" onChange={onFile}
          accept=".txt,.text,.list,.csv,.json,text/plain,text/csv,application/json"
          style={{ display: 'none' }}
        />
        <span style={{ fontSize: '11px', color: C.faint }}>
          {parsed.length} domain{parsed.length === 1 ? '' : 's'} · JSON, CSV, lines, or commas
        </span>
      </div>
    </div>
  );
}
