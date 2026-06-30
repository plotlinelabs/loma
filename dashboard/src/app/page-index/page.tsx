"use client";

import { useEffect, useState, useCallback, useMemo, useRef } from "react";

/* ── Types ─────────────────────────────────────────────────── */
interface IndexNode {
  title: string;
  summary?: string;
  text?: string;
  node_id?: string;
  line_num?: number;
  nodes: IndexNode[];
}
interface PageIndex {
  doc_name: string;
  doc_description: string;
  structure: IndexNode[];
}

interface LayoutNode {
  id: string;
  node: IndexNode;
  x: number;
  y: number;
  depth: number;
  parentId: string | null;
  childCount: number;
  leafCount: number;
  isExpanded: boolean;
  hasChildren: boolean;
}

/* ── Constants ────────────────────────────────────────────── */
const NODE_WIDTH = 200;
const NODE_HEIGHT = 44;
const H_GAP = 60;
const V_GAP = 8;
const LEVEL_WIDTH = NODE_WIDTH + H_GAP;

const CATEGORY_COLORS = [
  { fill: "#3B82F6", light: "#EFF6FF", border: "#93C5FD", text: "#1E40AF" },
  { fill: "#8B5CF6", light: "#F5F3FF", border: "#C4B5FD", text: "#5B21B6" },
  { fill: "#10B981", light: "#ECFDF5", border: "#6EE7B7", text: "#065F46" },
  { fill: "#F59E0B", light: "#FFFBEB", border: "#FCD34D", text: "#92400E" },
  { fill: "#EF4444", light: "#FEF2F2", border: "#FCA5A5", text: "#991B1B" },
  { fill: "#06B6D4", light: "#ECFEFF", border: "#67E8F9", text: "#155E75" },
  { fill: "#EC4899", light: "#FDF2F8", border: "#F9A8D4", text: "#9D174D" },
  { fill: "#14B8A6", light: "#F0FDFA", border: "#5EEAD4", text: "#134E4A" },
  { fill: "#F97316", light: "#FFF7ED", border: "#FDBA74", text: "#9A3412" },
  { fill: "#6366F1", light: "#EEF2FF", border: "#A5B4FC", text: "#3730A3" },
  { fill: "#84CC16", light: "#F7FEE7", border: "#BEF264", text: "#3F6212" },
];

function getCategoryColor(index: number) {
  return CATEGORY_COLORS[index % CATEGORY_COLORS.length];
}

function countDescendants(node: IndexNode): number {
  let c = 0;
  for (const child of node.nodes) c += 1 + countDescendants(child);
  return c;
}

function countLeaves(node: IndexNode): number {
  if (node.nodes.length === 0) return 1;
  return node.nodes.reduce((s, n) => s + countLeaves(n), 0);
}

/* ── Tree Layout Engine ──────────────────────────────────── */
function computeLayout(
  structure: IndexNode[],
  expandedSet: Set<string>,
  rootCategoryMap: Map<string, number>
): { nodes: LayoutNode[]; links: { from: string; to: string; categoryIndex: number }[]; width: number; height: number } {
  const nodes: LayoutNode[] = [];
  const links: { from: string; to: string; categoryIndex: number }[] = [];
  let currentY = 0;

  function layoutNode(node: IndexNode, id: string, depth: number, parentId: string | null): number {
    const hasChildren = node.nodes.length > 0;
    const isExpanded = expandedSet.has(id);
    const childCount = countDescendants(node);
    const leafCount = countLeaves(node);

    if (!hasChildren || !isExpanded) {
      const layoutN: LayoutNode = {
        id, node, x: depth * LEVEL_WIDTH, y: currentY,
        depth, parentId, childCount, leafCount,
        isExpanded, hasChildren,
      };
      nodes.push(layoutN);
      currentY += NODE_HEIGHT + V_GAP;
      return layoutN.y;
    }

    const startY = currentY;
    const childYs: number[] = [];

    node.nodes.forEach((child, i) => {
      const childId = `${id}/${i}`;
      const childMidY = layoutNode(child, childId, depth + 1, id);
      childYs.push(childMidY);
      const catIdx = rootCategoryMap.get(id.split("/")[0]) ?? 0;
      links.push({ from: id, to: childId, categoryIndex: catIdx });
    });

    const midY = (childYs[0] + childYs[childYs.length - 1]) / 2;

    const layoutN: LayoutNode = {
      id, node, x: depth * LEVEL_WIDTH, y: midY,
      depth, parentId, childCount, leafCount,
      isExpanded, hasChildren,
    };
    nodes.push(layoutN);
    return midY;
  }

  structure.forEach((section, i) => {
    rootCategoryMap.set(`${i}`, i);
    layoutNode(section, `${i}`, 0, null);
    currentY += 20;
  });

  const maxX = Math.max(...nodes.map((n) => n.x)) + NODE_WIDTH + 40;
  const maxY = currentY + 40;

  return { nodes, links, width: maxX, height: maxY };
}

/* ── Curved Link Path ────────────────────────────────────── */
function linkPath(x1: number, y1: number, x2: number, y2: number): string {
  const midX = x1 + (x2 - x1) * 0.5;
  return `M${x1},${y1} C${midX},${y1} ${midX},${y2} ${x2},${y2}`;
}

/* ── Graph Node Component ────────────────────────────────── */
function GraphNode({
  layout,
  categoryIndex,
  isSelected,
  onToggle,
  onSelect,
}: {
  layout: LayoutNode;
  categoryIndex: number;
  isSelected: boolean;
  onToggle: (id: string) => void;
  onSelect: (layout: LayoutNode) => void;
}) {
  const colors = getCategoryColor(categoryIndex);
  const { node, hasChildren, isExpanded, childCount } = layout;
  const isLeaf = !hasChildren;

  return (
    <g
      transform={`translate(${layout.x}, ${layout.y - NODE_HEIGHT / 2})`}
      className="cursor-pointer"
      onClick={(e) => {
        e.stopPropagation();
        onSelect(layout);
      }}
    >
      {/* Node background */}
      <rect
        width={NODE_WIDTH}
        height={NODE_HEIGHT}
        rx={10}
        ry={10}
        fill={isSelected ? colors.light : "#FFFFFF"}
        stroke={isSelected ? colors.fill : "#E5E7EB"}
        strokeWidth={isSelected ? 2 : 1}
        className="transition-all duration-200"
      />

      {/* Category accent bar */}
      <rect
        x={0}
        y={0}
        width={4}
        height={NODE_HEIGHT}
        rx={2}
        fill={colors.fill}
        opacity={layout.depth === 0 ? 1 : 0.5}
      />

      {/* Title text */}
      <foreignObject x={12} y={4} width={NODE_WIDTH - (hasChildren ? 48 : 20)} height={20}>
        <div
          style={{ color: isSelected ? colors.text : "#1F2937", fontSize: "12px", fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
          title={node.title}
        >
          {node.title}
        </div>
      </foreignObject>

      {/* Subtitle */}
      <foreignObject x={12} y={22} width={NODE_WIDTH - 20} height={16}>
        <div style={{ color: "#9CA3AF", fontSize: "10px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {isLeaf ? (node.summary || "").slice(0, 60) : `${childCount} items`}
        </div>
      </foreignObject>

      {/* Expand/collapse button */}
      {hasChildren && (
        <g
          transform={`translate(${NODE_WIDTH - 32}, ${NODE_HEIGHT / 2 - 10})`}
          onClick={(e) => {
            e.stopPropagation();
            onToggle(layout.id);
          }}
          className="cursor-pointer"
        >
          <rect width={20} height={20} rx={6} fill={colors.light} stroke={colors.border} strokeWidth={1} />
          <text
            x={10}
            y={14}
            textAnchor="middle"
            fontSize={14}
            fontWeight={700}
            fill={colors.fill}
          >
            {isExpanded ? "−" : "+"}
          </text>
        </g>
      )}

      {/* Leaf indicator dot */}
      {isLeaf && (
        <circle cx={NODE_WIDTH - 14} cy={NODE_HEIGHT / 2} r={4} fill={colors.fill} opacity={0.4} />
      )}
    </g>
  );
}

/* ── Detail Panel ────────────────────────────────────────── */
function DetailPanel({
  layout,
  categoryIndex,
  onClose,
}: {
  layout: LayoutNode;
  categoryIndex: number;
  onClose: () => void;
}) {
  const colors = getCategoryColor(categoryIndex);
  const { node } = layout;
  const [showFullText, setShowFullText] = useState(false);

  return (
    <div className="absolute top-4 right-4 w-96 max-h-[calc(100%-32px)] bg-white rounded-2xl shadow-2xl border border-gray-200 overflow-hidden flex flex-col z-20">
      {/* Header */}
      <div className="p-4 border-b border-gray-100" style={{ background: colors.light }}>
        <div className="flex items-start justify-between gap-2">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <span className="w-3 h-3 rounded-full flex-shrink-0" style={{ background: colors.fill }} />
              <h2 className="text-sm font-bold truncate" style={{ color: colors.text }}>{node.title}</h2>
            </div>
            {node.node_id && (
              <span className="text-[10px] font-mono text-gray-400">#{node.node_id}</span>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 p-1 rounded-lg hover:bg-white/50 transition-colors flex-shrink-0"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Stats */}
        <div className="grid grid-cols-3 gap-2">
          {[
            { label: "Children", value: layout.childCount },
            { label: "Leaves", value: layout.leafCount },
            { label: "Depth", value: layout.depth },
          ].map((s) => (
            <div key={s.label} className="bg-gray-50 rounded-lg p-2 text-center">
              <div className="text-base font-bold text-gray-900">{s.value}</div>
              <div className="text-[10px] text-gray-500">{s.label}</div>
            </div>
          ))}
        </div>

        {/* Summary */}
        {node.summary && (
          <div>
            <h3 className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-1">Summary</h3>
            <p className="text-xs text-gray-700 leading-relaxed">{node.summary}</p>
          </div>
        )}

        {/* Text */}
        {node.text && (
          <div>
            <div className="flex items-center justify-between mb-1">
              <h3 className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider">Content</h3>
              <button
                onClick={() => setShowFullText(!showFullText)}
                className="text-[10px] text-blue-500 hover:text-blue-700"
              >
                {showFullText ? "Collapse" : "Expand"}
              </button>
            </div>
            <pre className={`text-[11px] text-gray-600 bg-gray-50 rounded-lg p-3 whitespace-pre-wrap font-mono leading-relaxed overflow-y-auto ${showFullText ? "max-h-96" : "max-h-32"}`}>
              {node.text}
            </pre>
          </div>
        )}

        {/* Children list */}
        {node.nodes.length > 0 && (
          <div>
            <h3 className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider mb-2">
              Children ({node.nodes.length})
            </h3>
            <div className="space-y-1">
              {node.nodes.map((child, i) => (
                <div key={i} className="flex items-center gap-2 py-1 px-2 rounded-lg hover:bg-gray-50 text-xs text-gray-700">
                  <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: colors.fill, opacity: 0.5 }} />
                  <span className="truncate">{child.title}</span>
                  {child.nodes.length > 0 && (
                    <span className="text-[10px] text-gray-400 flex-shrink-0 ml-auto">+{countDescendants(child)}</span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Minimap ─────────────────────────────────────────────── */
function Minimap({
  nodes,
  canvasWidth,
  canvasHeight,
  viewBox,
  containerWidth,
  containerHeight,
  onNavigate,
}: {
  nodes: LayoutNode[];
  canvasWidth: number;
  canvasHeight: number;
  viewBox: { x: number; y: number; zoom: number };
  containerWidth: number;
  containerHeight: number;
  onNavigate: (x: number, y: number) => void;
}) {
  const mmWidth = 160;
  const mmHeight = 100;
  const scaleX = mmWidth / Math.max(canvasWidth, 1);
  const scaleY = mmHeight / Math.max(canvasHeight, 1);
  const scale = Math.min(scaleX, scaleY);

  const viewW = (containerWidth / viewBox.zoom) * scale;
  const viewH = (containerHeight / viewBox.zoom) * scale;
  const viewX = viewBox.x * scale;
  const viewY = viewBox.y * scale;

  return (
    <div className="absolute bottom-4 left-4 bg-white/90 backdrop-blur-sm rounded-xl border border-gray-200 shadow-lg p-2 z-20">
      <svg
        width={mmWidth}
        height={mmHeight}
        className="cursor-pointer"
        onClick={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const clickX = (e.clientX - rect.left) / scale;
          const clickY = (e.clientY - rect.top) / scale;
          onNavigate(clickX - containerWidth / viewBox.zoom / 2, clickY - containerHeight / viewBox.zoom / 2);
        }}
      >
        {/* Node dots */}
        {nodes.map((n) => (
          <rect
            key={n.id}
            x={n.x * scale}
            y={n.y * scale - 1}
            width={Math.max(NODE_WIDTH * scale, 2)}
            height={Math.max(2, NODE_HEIGHT * scale)}
            rx={1}
            fill={getCategoryColor(n.depth === 0 ? parseInt(n.id) : 0).fill}
            opacity={0.4}
          />
        ))}
        {/* Viewport rect */}
        <rect
          x={viewX}
          y={viewY}
          width={Math.max(viewW, 10)}
          height={Math.max(viewH, 10)}
          fill="rgba(59, 130, 246, 0.1)"
          stroke="#3B82F6"
          strokeWidth={1.5}
          rx={2}
        />
      </svg>
    </div>
  );
}

/* ── Main Page ───────────────────────────────────────────── */
export default function PageIndexPage() {
  const [data, setData] = useState<PageIndex | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selectedLayout, setSelectedLayout] = useState<LayoutNode | null>(null);
  const [viewBox, setViewBox] = useState({ x: -40, y: -40, zoom: 1 });
  const [isPanning, setIsPanning] = useState(false);
  const [panStart, setPanStart] = useState({ x: 0, y: 0, vx: 0, vy: 0 });
  const containerRef = useRef<HTMLDivElement>(null);
  const [containerSize, setContainerSize] = useState({ width: 1000, height: 600 });

  useEffect(() => {
    fetch("/page-index.json")
      .then((r) => r.json())
      .then((d: PageIndex) => {
        setData(d);
        const initial = new Set<string>();
        d.structure.forEach((_, i) => initial.add(`${i}`));
        setExpanded(initial);
      })
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const obs = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      setContainerSize({ width, height });
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  const rootCategoryMap = useMemo(() => new Map<string, number>(), []);

  const layout = useMemo(() => {
    if (!data) return null;
    rootCategoryMap.clear();
    return computeLayout(data.structure, expanded, rootCategoryMap);
  }, [data, expanded, rootCategoryMap]);

  const getCategoryIndex = useCallback(
    (id: string) => {
      const root = id.split("/")[0];
      return rootCategoryMap.get(root) ?? 0;
    },
    [rootCategoryMap]
  );

  const onToggle = useCallback((id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        // Collapse this and all children
        for (const key of next) {
          if (key === id || key.startsWith(id + "/")) next.delete(key);
        }
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const expandAll = useCallback(() => {
    if (!layout) return;
    const all = new Set<string>();
    layout.nodes.forEach((n) => { if (n.hasChildren) all.add(n.id); });
    // Need to recompute with all expanded - just expand from data
    if (!data) return;
    const allIds = new Set<string>();
    function walk(nodes: IndexNode[], prefix: string) {
      nodes.forEach((n, i) => {
        const p = prefix ? `${prefix}/${i}` : `${i}`;
        if (n.nodes.length > 0) allIds.add(p);
        walk(n.nodes, p);
      });
    }
    walk(data.structure, "");
    setExpanded(allIds);
  }, [data, layout]);

  const collapseAll = useCallback(() => {
    setExpanded(new Set());
  }, []);

  const resetView = useCallback(() => {
    setViewBox({ x: -40, y: -40, zoom: 1 });
  }, []);

  // Pan handlers
  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (e.button !== 0) return;
      setIsPanning(true);
      setPanStart({ x: e.clientX, y: e.clientY, vx: viewBox.x, vy: viewBox.y });
    },
    [viewBox]
  );

  const onMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!isPanning) return;
      const dx = (e.clientX - panStart.x) / viewBox.zoom;
      const dy = (e.clientY - panStart.y) / viewBox.zoom;
      setViewBox((v) => ({ ...v, x: panStart.vx - dx, y: panStart.vy - dy }));
    },
    [isPanning, panStart, viewBox.zoom]
  );

  const onMouseUp = useCallback(() => setIsPanning(false), []);

  const onWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    const delta = e.deltaY > 0 ? 0.9 : 1.1;
    setViewBox((v) => {
      const newZoom = Math.min(Math.max(v.zoom * delta, 0.1), 3);
      // Zoom toward mouse position
      const rect = containerRef.current?.getBoundingClientRect();
      if (rect) {
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;
        const worldX = v.x + mouseX / v.zoom;
        const worldY = v.y + mouseY / v.zoom;
        const newX = worldX - mouseX / newZoom;
        const newY = worldY - mouseY / newZoom;
        return { x: newX, y: newY, zoom: newZoom };
      }
      return { ...v, zoom: newZoom };
    });
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-sm text-gray-400">Loading page index...</div>
      </div>
    );
  }

  if (!data || !layout) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 text-sm">
        Failed to load. Ensure <code className="bg-gray-100 px-1.5 py-0.5 rounded mx-1">page-index.json</code> exists.
      </div>
    );
  }

  const svgViewBox = `${viewBox.x} ${viewBox.y} ${containerSize.width / viewBox.zoom} ${containerSize.height / viewBox.zoom}`;

  return (
    <div className="h-full flex flex-col animate-fade-in-up">
      {/* Top bar */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 bg-white flex-shrink-0">
        <div className="flex items-center gap-4">
          <div>
            <h1 className="text-sm font-semibold text-gray-900">Page Index</h1>
            <p className="text-[11px] text-gray-400">{data.doc_description}</p>
          </div>
          <div className="flex items-center gap-3 ml-4">
            {[
              { label: "Nodes", value: layout.nodes.length },
              { label: "Categories", value: data.structure.length },
            ].map((s) => (
              <div key={s.label} className="flex items-center gap-1.5">
                <span className="text-sm font-bold text-gray-900">{s.value}</span>
                <span className="text-[10px] text-gray-400">{s.label}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-1">
          {/* Category legend */}
          <div className="flex items-center gap-2 mr-4">
            {data.structure.slice(0, 6).map((s, i) => (
              <div key={i} className="flex items-center gap-1" title={s.title}>
                <span className="w-2 h-2 rounded-full" style={{ background: getCategoryColor(i).fill }} />
                <span className="text-[10px] text-gray-400 hidden xl:inline max-w-[80px] truncate">{s.title}</span>
              </div>
            ))}
            {data.structure.length > 6 && (
              <span className="text-[10px] text-gray-400">+{data.structure.length - 6}</span>
            )}
          </div>

          <button onClick={expandAll} className="text-[11px] text-gray-500 hover:text-gray-700 px-2 py-1 rounded-lg hover:bg-gray-100 transition-colors">
            Expand
          </button>
          <button onClick={collapseAll} className="text-[11px] text-gray-500 hover:text-gray-700 px-2 py-1 rounded-lg hover:bg-gray-100 transition-colors">
            Collapse
          </button>
          <div className="w-px h-4 bg-gray-200 mx-1" />
          <button onClick={() => setViewBox((v) => ({ ...v, zoom: Math.min(v.zoom * 1.2, 3) }))} className="text-gray-500 hover:text-gray-700 p-1 rounded-lg hover:bg-gray-100 transition-colors">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" /></svg>
          </button>
          <button onClick={() => setViewBox((v) => ({ ...v, zoom: Math.max(v.zoom * 0.8, 0.1) }))} className="text-gray-500 hover:text-gray-700 p-1 rounded-lg hover:bg-gray-100 transition-colors">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" d="M5 12h14" /></svg>
          </button>
          <button onClick={resetView} className="text-[11px] text-gray-500 hover:text-gray-700 px-2 py-1 rounded-lg hover:bg-gray-100 transition-colors">
            Reset
          </button>
          <span className="text-[10px] text-gray-400 ml-1">{Math.round(viewBox.zoom * 100)}%</span>
        </div>
      </div>

      {/* Graph canvas */}
      <div
        ref={containerRef}
        className="flex-1 relative overflow-hidden bg-gray-50"
        style={{ cursor: isPanning ? "grabbing" : "grab" }}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
        onWheel={onWheel}
      >
        <svg
          width="100%"
          height="100%"
          viewBox={svgViewBox}
          className="select-none"
        >
          {/* Grid pattern */}
          <defs>
            <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
              <circle cx="20" cy="20" r="0.5" fill="#D1D5DB" opacity="0.5" />
            </pattern>
          </defs>
          <rect x={viewBox.x} y={viewBox.y} width={containerSize.width / viewBox.zoom} height={containerSize.height / viewBox.zoom} fill="url(#grid)" />

          {/* Links */}
          <g>
            {layout.links.map((link, i) => {
              const fromNode = layout.nodes.find((n) => n.id === link.from);
              const toNode = layout.nodes.find((n) => n.id === link.to);
              if (!fromNode || !toNode) return null;
              const colors = getCategoryColor(link.categoryIndex);
              return (
                <path
                  key={i}
                  d={linkPath(
                    fromNode.x + NODE_WIDTH,
                    fromNode.y,
                    toNode.x,
                    toNode.y
                  )}
                  fill="none"
                  stroke={colors.fill}
                  strokeWidth={1.5}
                  strokeOpacity={0.25}
                />
              );
            })}
          </g>

          {/* Nodes */}
          <g>
            {layout.nodes.map((n) => (
              <GraphNode
                key={n.id}
                layout={n}
                categoryIndex={getCategoryIndex(n.id)}
                isSelected={selectedLayout?.id === n.id}
                onToggle={onToggle}
                onSelect={(l) => setSelectedLayout(l)}
              />
            ))}
          </g>
        </svg>

        {/* Detail panel */}
        {selectedLayout && (
          <DetailPanel
            layout={selectedLayout}
            categoryIndex={getCategoryIndex(selectedLayout.id)}
            onClose={() => setSelectedLayout(null)}
          />
        )}

        {/* Minimap */}
        <Minimap
          nodes={layout.nodes}
          canvasWidth={layout.width}
          canvasHeight={layout.height}
          viewBox={viewBox}
          containerWidth={containerSize.width}
          containerHeight={containerSize.height}
          onNavigate={(x, y) => setViewBox((v) => ({ ...v, x, y }))}
        />

        {/* Zoom hint */}
        <div className="absolute bottom-4 right-4 text-[10px] text-gray-400 bg-white/80 backdrop-blur-sm px-2 py-1 rounded-lg z-10">
          Scroll to zoom &middot; Drag to pan
        </div>
      </div>
    </div>
  );
}
