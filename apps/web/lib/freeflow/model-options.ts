/**
 * Adapted from one-api's custom model option merging:
 * songquanpeng/one-api, web/default/src/pages/Channel/EditChannel.js
 * commit 8df4a2670b98266bd287c698243fff327d9748cf (MIT).
 * Copyright (c) 2023 JustSong. See THIRD_PARTY_NOTICES.md at repository root.
 */
export function mergeModelOptions(
  discovered: string[],
  selected: string[],
): string[] {
  const options = [...new Set(discovered)];
  selected.forEach((model) => {
    if (model && !options.find((option) => option === model))
      options.push(model);
  });
  return options;
}

export function normalizeApiAddress(raw: string): string {
  const address = raw.trim().replace(/\/+$/, "");
  const base = address.replace(/\/(chat\/completions|images\/generations|models)$/, "");
  try {
    const url = new URL(base);
    return !url.pathname || url.pathname === "/" ? `${base}/v1` : base;
  } catch {
    return base;
  }
}

export function connectionLabel(label: string, address: string): string {
  if (label.trim()) return label.trim();
  try {
    return new URL(address.trim()).hostname.slice(0, 64);
  } catch {
    return "自定义 API";
  }
}
