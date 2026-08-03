// icons.jsx — inline SVG icons matching the prototype's stroke set.
// Each accepts { size, color, ...rest }; defaults stroke=currentColor.

const stroke = (size, children, extra = {}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="2"
    stroke-linecap="round"
    stroke-linejoin="round"
    style={extra}
  >
    {children}
  </svg>
);

export const SearchIcon = ({ size = 16, color = '#0B0D13' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color}
       stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" />
  </svg>
);

export const GearIcon = ({ size = 15 }) => stroke(size, (
  <>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" />
  </>
));

export const BackIcon = ({ size = 16 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
       stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M19 12H5M12 19l-7-7 7-7" />
  </svg>
);

export const PauseIcon = ({ size = 15 }) => (
  <svg width="100%" height="100%" viewBox="0 0 24 24" fill="currentColor">
    <rect x="6" y="5" width="4" height="14" rx="1" /><rect x="14" y="5" width="4" height="14" rx="1" />
  </svg>
);

export const PlayIcon = () => (
  <svg width="100%" height="100%" viewBox="0 0 24 24" fill="currentColor">
    <path d="M6 4l14 8-14 8V4z" />
  </svg>
);

export const DownloadIcon = ({ size = 14 }) => stroke(size, (
  <><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="M7 10l5 5 5-5" /><path d="M12 15V3" /></>
));

export const ArrowRightIcon = ({ size = 14 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
       stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
    <path d="M5 12h14M13 6l6 6-6 6" />
  </svg>
);

export const LinkIcon = ({ size = 13, color = '#5C6680' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color}
       stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M9 17H7A5 5 0 0 1 7 7h2M15 7h2a5 5 0 0 1 0 10h-2M8 12h8" />
  </svg>
);

export const GlobeIcon = ({ size = 13, color = '#5C6680' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color}
       stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
    <circle cx="12" cy="12" r="9" /><path d="M3.6 9h16.8M3.6 15h16.8M12 3a15 15 0 0 1 0 18M12 3a15 15 0 0 0 0 18" />
  </svg>
);

export const EyeOffIcon = ({ size = 13, color = '#5C6680' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color}
       stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M9.9 4.2A9.1 9.1 0 0 1 12 4c7 0 10 8 10 8a13.2 13.2 0 0 1-1.7 2.7" />
    <path d="M6.6 6.6A13.5 13.5 0 0 0 2 12s3 8 10 8a9 9 0 0 0 5.4-1.6" />
    <path d="m2 2 20 20" />
  </svg>
);

export const EyeIcon = () => stroke(15, (
  <><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" /><circle cx="12" cy="12" r="3" /></>
));

export const EyeOffSmall = () => stroke(15, (
  <>
    <path d="M9.9 4.2A9.1 9.1 0 0 1 12 4c7 0 10 8 10 8a13.2 13.2 0 0 1-1.7 2.7" />
    <path d="M6.6 6.6A13.5 13.5 0 0 0 2 12s3 8 10 8a9 9 0 0 0 5.4-1.6" />
    <path d="m2 2 20 20" />
  </>
));

export const PulseIcon = ({ size = 14 }) => stroke(size, <path d="M22 12h-4l-3 9L9 3l-3 9H2" />);

export const SpinnerIcon = ({ size = 14 }) => (
  <svg width="100%" height="100%" viewBox="0 0 24 24" fill="none" stroke="currentColor"
       stroke-width="2" style={{ animation: 'spin 1s linear infinite' }}>
    <path d="M21 12a9 9 0 1 1-6.2-8.5" stroke-linecap="round" />
  </svg>
);

export const CheckIcon = ({ size = 13 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
       stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
    <path d="M20 6 9 17l-5-5" />
  </svg>
);

export const CloseIcon = ({ size = 13 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
       stroke-width="2.2" stroke-linecap="round">
    <path d="M18 6 6 18M6 6l12 12" />
  </svg>
);
