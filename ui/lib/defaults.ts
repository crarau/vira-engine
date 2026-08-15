/**
 * Default product per company, so the form is runnable on arrival.
 *
 * An empty product field is not a neutral starting point — it invites "chips",
 * and the measured cost of that is real: "Selling chips" scored 2.6, while a
 * product that names its mechanism scored 3.8. The default is therefore written
 * the way a good input looks, not as a placeholder to be replaced.
 *
 * Companies not listed here fall back to whatever the corpus knows about them:
 * the first ad theme, then the first keyword, then the bio's opening clause.
 * That is usually weaker than a hand-written default, which is exactly why the
 * good ones are pinned.
 */

export const PRODUCT_DEFAULTS: Record<string, string> = {
  "sunday-oats": "Cocoa Hazelnut overnight oats — assembled in ten seconds the night before",
  "eli-health": "the Hormometer cortisol test — lab-grade hormone reading at home in 20 minutes",
  overcast: "a mineral SPF 50 serum that leaves no white cast on deeper skin tones",
  bramble: "a slow-release treat dispenser that keeps anxious dogs settled when left alone",
  rebull: "a zero-sugar blood orange ginseng energy drink for the 5am build",
  chips: "a single-flavour spicy chip, fried in small batches",
  squirt: "a citrus soda with half the sugar and none of the aftertaste",
  vira: "an ad engine that turns real trending video into a shootable script",
};

/** What the corpus already knows, when we have no pinned default. */
export type CompanyLike = {
  slug?: string;
  name?: string;
  bio?: string;
  ad_themes?: string[] | null;
  keywords?: string[] | null;
};

export function defaultProduct(company: CompanyLike | null | undefined): string {
  if (!company) return "";
  if (company.slug && PRODUCT_DEFAULTS[company.slug]) {
    return PRODUCT_DEFAULTS[company.slug];
  }
  const theme = company.ad_themes?.[0]?.trim();
  if (theme) return theme;

  const keyword = company.keywords?.[0]?.trim();
  if (keyword) return keyword;

  // First clause of the bio. Crude, but it beats an empty box, and the helper
  // text under the field already tells the user why specificity pays.
  const clause = company.bio?.split(/[.—–]/)[0]?.trim();
  if (clause && clause.length > 8) return clause.slice(0, 140);

  return company.name ?? "";
}
