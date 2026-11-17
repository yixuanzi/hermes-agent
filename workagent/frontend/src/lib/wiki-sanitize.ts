import { defaultSchema } from "rehype-sanitize";
import type { Options as SanitizeSchema } from "rehype-sanitize";

/**
 * Wiki documents render raw HTML (rehype-raw) and their content can come from ingested,
 * untrusted sources, so the tree is sanitized afterwards with GitHub's schema. The extras
 * are exactly what remark-obsidian emits: <mark> highlights and callout blockquotes.
 */
export const wikiSanitizeSchema: SanitizeSchema = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), "mark"],
  attributes: {
    ...defaultSchema.attributes,
    blockquote: [
      ...(defaultSchema.attributes?.blockquote ?? []),
      "dataCallout",
      ["className", /^callout(-\w+)?$/],
    ],
  },
};
