"""Runtime JS capture (CDP): drive the baked-in headless Chromium to capture the
scripts a page actually EXECUTES, reaching inline / runtime-injected / eval'd JS
the static fetch stage cannot see. See ``driver`` (the CDP mechanism) and ``stage``
(the discover-stage entry that stores captures as the existing asset contract)."""
