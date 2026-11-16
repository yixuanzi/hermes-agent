/* eslint-disable @typescript-eslint/no-explicit-any */
import { visit } from "unist-util-visit";

type ObsidianOptions = {
  enableWikiLinks?: boolean;
  enableCallouts?: boolean;
  enableHighlights?: boolean;
};

export default function remarkObsidian(this: any, opts?: ObsidianOptions) {
  const options = {
    enableWikiLinks: true,
    enableCallouts: true,
    enableHighlights: true,
    ...opts,
  };

  return (tree: any) => {
    if (options.enableHighlights) {
      visit(tree, "text", (node: any) => {
        if (!node.value.includes("==")) return;
        const parts: string[] = node.value.split(/==(.*?)==/g);
        if (parts.length <= 1) return;
        // walk up to parent via tree — visitor gives us the node only
        replaceInParent(tree, node, parts, (part) => ({ type: "html", value: `<mark>${part}</mark>` }));
      });
    }

    if (options.enableWikiLinks) {
      visit(tree, "text", (node: any) => {
        if (!node.value.includes("[[")) return;
        const parts: string[] = node.value.split(/\[\[(.*?)\]\]/g);
        if (parts.length <= 1) return;
        replaceInParent(tree, node, parts, (part) => {
          const pipeIdx = part.indexOf("|");
          const display = pipeIdx >= 0 ? part.slice(pipeIdx + 1) : part;
          const target = pipeIdx >= 0 ? part.slice(0, pipeIdx) : part;
          return {
            type: "link",
            url: `#${encodeURIComponent(target)}`,
            title: target,
            children: [{ type: "text", value: display }],
          };
        });
      });
    }

    if (options.enableCallouts) {
      visit(tree, "blockquote", (node: any) => {
        if (node.children.length === 0) return;
        const first = node.children[0];
        if (!first || first.type !== "paragraph") return;
        const textNode = first.children?.[0];
        if (!textNode || textNode.type !== "text") return;
        const m = textNode.value.match(/^\[!(\w+)\]\s*(.*)/s);
        if (!m) return;
        const calloutType = m[1].toLowerCase();
        const rest = m[2] ?? "";
        if (rest) {
          textNode.value = rest;
        } else {
          first.children.shift();
          if (first.children.length === 0) node.children.shift();
        }
        node.data = {
          ...(node.data || {}),
          hProperties: { "data-callout": calloutType, className: `callout callout-${calloutType}` },
        };
      });
    }
  };
}

function replaceInParent(tree: any, target: any, parts: string[], makeTag: (text: string) => any) {
  visit(tree, (node: any) => {
    if (!node || !Array.isArray(node.children)) return;
    const idx = node.children.indexOf(target);
    if (idx === -1) return;
    const newNodes: any[] = [];
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      if (!part) continue;
      newNodes.push(i % 2 === 0 ? { type: "text", value: part } : makeTag(part));
    }
    node.children.splice(idx, 1, ...newNodes);
  });
}
