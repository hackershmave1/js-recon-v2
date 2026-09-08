// Deliberately hidden. Cheap decoding passes are worth it — these are usually the good ones.
const _0x4a2f = [
  "aHR0cHM6Ly9hcGkuYWNtZS5pby92Mi9hZG1pbi9pbXBlcnNvbmF0ZQ==", // https://api.acme.io/v2/admin/impersonate
  "L2FwaS9pbnRlcm5hbC9kZWJ1Zy9kdW1w",                         // /api/internal/debug/dump
];
fetch(atob(_0x4a2f[0]), { method: "POST" });
fetch(atob(_0x4a2f[1]));

const p = "\x2f\x61\x70\x69\x2f\x76\x31\x2f\x73\x65\x63\x72\x65\x74\x73"; // /api/v1/secrets
fetch(HOST + p);

const seg = ["\u002fapi", "v1", "keys", "rotate"].join("/");
fetch(BASE + seg, { method: "POST" });

const h = String.fromCharCode(47,97,112,105,47,118,49,47,102,108,97,103,115); // /api/v1/flags
fetch(h);

const rev = "pmud/gubed/lanretni/ipa/".split("").reverse().join(""); // /api/internal/debug/dump
fetch(rev);
