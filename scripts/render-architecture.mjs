#!/usr/bin/env node

/**
 * 从受控的 diagrams.net XML 图源生成纯 SVG。
 *
 * 该脚本只实现本项目架构图需要的 mxCell 子集，不尝试替代 diagrams.net 完整渲染器。
 * 图源仍可直接在 diagrams.net 中打开编辑；生成结果不依赖外部字体、图片或网络资源。
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const defaultSource = path.resolve(
  scriptDirectory,
  "../docs/assets/architecture/system-architecture.drawio",
);
const defaultTarget = path.resolve(
  scriptDirectory,
  "../docs/assets/architecture/system-architecture.svg",
);

const sourcePath = path.resolve(process.argv[2] ?? defaultSource);
const targetPath = path.resolve(process.argv[3] ?? defaultTarget);
const source = fs.readFileSync(sourcePath, "utf8");

function decodeXml(value) {
  return value
    .replaceAll("&#xa;", "\n")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&amp;", "&");
}

function escapeXml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function parseAttributes(fragment) {
  const attributes = {};
  for (const match of fragment.matchAll(/([A-Za-z][\w-]*)="([^"]*)"/g)) {
    attributes[match[1]] = decodeXml(match[2]);
  }
  return attributes;
}

function parseStyle(styleValue = "") {
  const style = {};
  for (const item of styleValue.split(";")) {
    if (!item) continue;
    const [key, value = "1"] = item.split("=", 2);
    style[key] = value;
  }
  return style;
}

const vertices = new Map();
const edges = [];
const cellPattern = /<mxCell\s+([^>]*?)(?<!\/)>([\s\S]*?)<\/mxCell>/g;

for (const match of source.matchAll(cellPattern)) {
  const attributes = parseAttributes(match[1]);
  const geometryMatch = match[2].match(/<mxGeometry\s+([^>]*?)(?:\/>|>)/);
  const geometry = geometryMatch ? parseAttributes(geometryMatch[1]) : {};
  const style = parseStyle(attributes.style);

  if (attributes.vertex === "1") {
    vertices.set(attributes.id, {
      id: attributes.id,
      label: attributes.value ?? "",
      x: Number(geometry.x ?? 0),
      y: Number(geometry.y ?? 0),
      width: Number(geometry.width ?? 0),
      height: Number(geometry.height ?? 0),
      style,
    });
  }

  if (attributes.edge === "1") {
    const points = [...match[2].matchAll(/<mxPoint\s+([^>]*?)(?:\/>|>)/g)].map(
      (pointMatch) => {
        const point = parseAttributes(pointMatch[1]);
        return { x: Number(point.x ?? 0), y: Number(point.y ?? 0) };
      },
    );
    edges.push({
      id: attributes.id,
      label: attributes.value ?? "",
      source: attributes.source,
      target: attributes.target,
      labelX: attributes.labelX === undefined ? undefined : Number(attributes.labelX),
      labelY: attributes.labelY === undefined ? undefined : Number(attributes.labelY),
      points,
      style,
    });
  }
}

if (vertices.size === 0) {
  throw new Error(`未从 ${sourcePath} 读取到任何 vertex`);
}

function boundaryPoint(from, to) {
  const fromCenter = {
    x: from.x + from.width / 2,
    y: from.y + from.height / 2,
  };
  const toCenter = {
    x: to.x + to.width / 2,
    y: to.y + to.height / 2,
  };
  const dx = toCenter.x - fromCenter.x;
  const dy = toCenter.y - fromCenter.y;
  if (dx === 0 && dy === 0) return fromCenter;

  const xScale = dx === 0 ? Number.POSITIVE_INFINITY : from.width / 2 / Math.abs(dx);
  const yScale = dy === 0 ? Number.POSITIVE_INFINITY : from.height / 2 / Math.abs(dy);
  const scale = Math.min(xScale, yScale);
  return {
    x: fromCenter.x + dx * scale,
    y: fromCenter.y + dy * scale,
  };
}

function svgText(vertex) {
  if (!vertex.label) return "";
  const lines = vertex.label.split("\n");
  const fontSize = Number(vertex.style.fontSize ?? 14);
  const fontWeight = vertex.style.fontStyle === "1" ? 650 : 500;
  const color = vertex.style.fontColor ?? "#172033";
  const lineHeight = fontSize * 1.45;
  const topAligned = vertex.style.verticalAlign === "top";
  const firstLineY = topAligned
    ? vertex.y + Number(vertex.style.spacingTop ?? 10) + fontSize
    : vertex.y + vertex.height / 2 - ((lines.length - 1) * lineHeight) / 2 + fontSize * 0.35;
  const textX = vertex.x + vertex.width / 2;

  const spans = lines
    .map(
      (line, index) =>
        `<tspan x="${textX}" y="${firstLineY + index * lineHeight}">${escapeXml(line)}</tspan>`,
    )
    .join("");

  return `<text text-anchor="middle" font-family="Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft YaHei', sans-serif" font-size="${fontSize}" font-weight="${fontWeight}" fill="${color}">${spans}</text>`;
}

function svgVertex(vertex) {
  if (vertex.style.strokeColor === "none" && vertex.style.fillColor === "none") {
    return svgText(vertex);
  }

  const fill = vertex.style.fillColor ?? "#FFFFFF";
  const stroke = vertex.style.strokeColor ?? "#7A8798";
  const strokeWidth = Number(vertex.style.strokeWidth ?? 1);
  const radius = vertex.style.rounded === "1" ? 14 : 3;
  const filter = vertex.style.shadow === "1" ? ' filter="url(#soft-shadow)"' : "";
  return `<g><rect x="${vertex.x}" y="${vertex.y}" width="${vertex.width}" height="${vertex.height}" rx="${radius}" fill="${fill}" stroke="${stroke}" stroke-width="${strokeWidth}"${filter}/>${svgText(vertex)}</g>`;
}

function svgEdge(edge) {
  const sourceVertex = vertices.get(edge.source);
  const targetVertex = vertices.get(edge.target);
  if (!sourceVertex || !targetVertex) return "";

  const sourceDirection = edge.points[0] ?? {
    x: targetVertex.x + targetVertex.width / 2,
    y: targetVertex.y + targetVertex.height / 2,
  };
  const targetDirection = edge.points.at(-1) ?? {
    x: sourceVertex.x + sourceVertex.width / 2,
    y: sourceVertex.y + sourceVertex.height / 2,
  };
  const start = boundaryPoint(sourceVertex, {
    x: sourceDirection.x,
    y: sourceDirection.y,
    width: 0,
    height: 0,
  });
  const end = boundaryPoint(targetVertex, {
    x: targetDirection.x,
    y: targetDirection.y,
    width: 0,
    height: 0,
  });
  const stroke = edge.style.strokeColor ?? "#506784";
  const strokeWidth = Number(edge.style.strokeWidth ?? 2);
  const dashed = edge.style.dashed === "1" ? ' stroke-dasharray="8 7"' : "";
  const markerStart = edge.style.startArrow === "classic" ? ' marker-start="url(#arrow-start)"' : "";
  const markerEnd = edge.style.endArrow === "classic" ? ' marker-end="url(#arrow-end)"' : "";
  const pathPoints = [start, ...edge.points, end];
  const pathData = pathPoints
    .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`)
    .join(" ");

  return `<path d="${pathData}" fill="none" stroke="${stroke}" stroke-width="${strokeWidth}" stroke-linejoin="round"${dashed}${markerStart}${markerEnd}/>`;
}

function svgEdgeLabel(edge) {
  if (!edge.label) return "";
  const sourceVertex = vertices.get(edge.source);
  const targetVertex = vertices.get(edge.target);
  if (!sourceVertex || !targetVertex) return "";

  const fallbackX =
    (sourceVertex.x + sourceVertex.width / 2 + targetVertex.x + targetVertex.width / 2) / 2;
  const fallbackY =
    (sourceVertex.y + sourceVertex.height / 2 + targetVertex.y + targetVertex.height / 2) / 2 - 9;
  const labelX = edge.labelX ?? fallbackX;
  const labelY = edge.labelY ?? fallbackY;
  const labelWidth = Math.max(64, [...edge.label].length * 7.1 + 16);

  return `<g><rect x="${labelX - labelWidth / 2}" y="${labelY - 13}" width="${labelWidth}" height="20" rx="6" fill="#FFFFFF" fill-opacity="0.96"/><text x="${labelX}" y="${labelY + 1}" text-anchor="middle" font-family="Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft YaHei', sans-serif" font-size="11" font-weight="600" fill="#44546A">${escapeXml(edge.label)}</text></g>`;
}

const orderedVertices = [...vertices.values()].sort(
  (left, right) => right.width * right.height - left.width * left.height,
);
const svg = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1440" height="920" viewBox="0 0 1440 920" role="img" aria-labelledby="title description">
  <title id="title">可靠文献研究与 Research Agent 系统架构</title>
  <desc id="description">展示 React Web、FastAPI、PostgreSQL、Valkey ARQ、Worker、外部 Provider、Storage 与 Session 专属 OpenSandbox 的边界和数据流。</desc>
  <defs>
    <filter id="soft-shadow" x="-20%" y="-20%" width="140%" height="150%">
      <feDropShadow dx="0" dy="4" stdDeviation="5" flood-color="#172033" flood-opacity="0.10"/>
    </filter>
    <marker id="arrow-end" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke"/>
    </marker>
    <marker id="arrow-start" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M 10 0 L 0 5 L 10 10 z" fill="context-stroke"/>
    </marker>
  </defs>
  <rect width="1440" height="920" fill="#FFFFFF"/>
  ${edges.map(svgEdge).join("\n  ")}
  ${orderedVertices.map(svgVertex).join("\n  ")}
  ${edges.map(svgEdgeLabel).join("\n  ")}
</svg>
`;

fs.writeFileSync(targetPath, svg, "utf8");
console.log(`已生成 ${path.relative(process.cwd(), targetPath)}`);
