// icons.jsx — inline SVG icons for the workspace, matching the prototype's stroke set.
const S = (children, { size = 18, w = 2 } = {}) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
       stroke-width={w} stroke-linecap="round" stroke-linejoin="round">{children}</svg>
);

export const Logo = ({ size = 17 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="#0B0D13"
       stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" />
  </svg>
);

export const SearchIcon = ({ size = 15 }) => S(<><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></>, { size });
export const ExportIcon = ({ size = 15 }) => S(<><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="M7 10l5 5 5-5" /><path d="M12 15V3" /></>, { size });
export const PlusIcon = ({ size = 15 }) => S(<path d="M12 5v14M5 12h14" />, { size, w: 2.4 });
export const RefreshIcon = ({ size = 14 }) => S(<><path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5" /></>, { size });
export const ChevronDown = ({ size = 13 }) => S(<path d="m6 9 6 6 6-6" />, { size, w: 2.2 });
export const ActivityIcon = ({ size = 18 }) => S(<path d="M22 12h-4l-3 9L9 3l-3 9H2" />, { size });
export const FileIcon = ({ size = 14 }) => S(<><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /></>, { size });
export const KeyIcon = ({ size = 16 }) => S(<><circle cx="7.5" cy="15.5" r="4.5" /><path d="m10.7 12.3 8.3-8.3" /><path d="m17 5 3 3" /></>, { size });
export const CheckCircle = ({ size = 16 }) => S(<><path d="M22 11.1V12a10 10 0 1 1-5.9-9.1" /><path d="M22 4 12 14.01l-3-3" /></>, { size });
export const ArrowRight = ({ size = 14 }) => S(<path d="M5 12h14M13 6l6 6-6 6" />, { size, w: 2.2 });
export const CopyIcon = ({ size = 14 }) => S(<><rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></>, { size });
export const CloseIcon = ({ size = 15 }) => S(<path d="M18 6 6 18M6 6l12 12" />, { size, w: 2.2 });
export const CheckIcon = ({ size = 10 }) => S(<path d="M20 6 9 17l-5-5" />, { size, w: 3 });
export const FocusIcon = ({ size = 14 }) => S(<><circle cx="12" cy="12" r="3" /><path d="M12 3v2M12 19v2M3 12h2M19 12h2" /></>, { size });
export const AlertIcon = ({ size = 15 }) => S(<><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" /><path d="M12 9v4M12 17h.01" /></>, { size });
export const DownloadIcon = ({ size = 13 }) => S(<><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="M7 10l5 5 5-5" /><path d="M12 15V3" /></>, { size });
export const FolderIcon = ({ size = 14 }) => S(<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9L9.6 3.9A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z" />, { size });
export const ChevronRight = ({ size = 13 }) => S(<path d="m9 18 6-6-6-6" />, { size, w: 2.2 });
export const ChevronLeft = ({ size = 13 }) => S(<path d="m15 18-6-6 6-6" />, { size, w: 2.2 });
export const PlayIcon = ({ size = 15 }) => S(<path d="m5 3 14 9-14 9V3z" />, { size, w: 2 });
export const StopIcon = ({ size = 13 }) => S(<rect x="6" y="6" width="12" height="12" rx="2" />, { size });
export const ClockIcon = ({ size = 13 }) => S(<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>, { size });
export const InfoIcon = ({ size = 17 }) => S(<><circle cx="12" cy="12" r="9" /><path d="M12 16v-4M12 8h.01" /></>, { size });
export const EditIcon = ({ size = 13 }) => S(<><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></>, { size });
export const TrashIcon = ({ size = 13 }) => S(<><path d="M3 6h18" /><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" /></>, { size });

export const NAV_ICONS = {
  projects: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9L9.6 3.9A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z" />
    </svg>
  ),
  overview: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" />
    </svg>
  ),
  findings: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" /><path d="M12 9v4M12 17h.01" />
    </svg>
  ),
  sources: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="m16 18 6-6-6-6" /><path d="m8 6-6 6 6 6" />
    </svg>
  ),
  sessions: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M21 16V8a2 2 0 0 0-1-1.7l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.7l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z" />
    </svg>
  )
};

// Metric-card icons (match prototype)
export const METRIC_ICONS = {
  files: <FileIcon size={15} />,
  endpoints: (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l3 8 4-16 3 8h4" /></svg>
  ),
  secrets: <KeyIcon size={15} />,
  coverage: <CheckCircle size={15} />
};
