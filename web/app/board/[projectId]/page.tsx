'use client';

import '@xyflow/react/dist/style.css';
import Link from 'next/link';
import { use, useEffect, useMemo, useState } from 'react';
import { Background, Controls, MiniMap, ReactFlow, type Edge, type Node } from '@xyflow/react';

// Production is served behind an HTTPS ingress that routes API paths to the
// API service; local development keeps a direct API fallback.
const API = process.env.NEXT_PUBLIC_API_BASE ?? (process.env.NODE_ENV === 'production' ? '' : 'http://127.0.0.1:8787');

type GraphPayload = { nodes: Array<{ id: string; type: string; entity_id: string; label: string; status: string }>; edges: Array<{ id: string; source: string; target: string; relation: string }> };

type StatusFilter = 'all' | 'incomplete' | 'ready';
type TypeFilter = 'all' | 'story' | 'production' | 'asset';

const COMPLETE_STATUSES = new Set(['normal', 'ready', 'approved', 'completed']);

function isIncomplete(status: string): boolean {
  return !COMPLETE_STATUSES.has(status);
}

function nodeGroup(type: string): Exclude<TypeFilter, 'all'> {
  if (['location', 'prop', 'character'].includes(type)) return 'story';
  if (['project', 'episode', 'shot'].includes(type)) return 'production';
  return 'asset';
}

async function getGraph(projectId: string): Promise<GraphPayload> {
  const response = await fetch(`${API}/api/projects/${projectId}/graph`, { credentials: 'include' });
  if (!response.ok) throw new Error('关系图加载失败');
  return response.json();
}

export default function BoardPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = use(params);
  const [graph, setGraph] = useState<GraphPayload | null>(null);
  const [error, setError] = useState('');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all');
  useEffect(() => { getGraph(projectId).then(setGraph).catch((reason: Error) => setError(reason.message)); }, [projectId]);
  const filteredGraph = useMemo(() => {
    const sourceNodes = graph?.nodes ?? [];
    const visibleNodes = sourceNodes.filter((node) => {
      const statusMatches = statusFilter === 'all'
        || (statusFilter === 'incomplete' && isIncomplete(node.status))
        || (statusFilter === 'ready' && !isIncomplete(node.status));
      const typeMatches = typeFilter === 'all' || nodeGroup(node.type) === typeFilter;
      return statusMatches && typeMatches;
    });
    const visibleIds = new Set(visibleNodes.map((node) => node.id));
    return {
      nodes: visibleNodes,
      edges: (graph?.edges ?? []).filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target)),
    };
  }, [graph, statusFilter, typeFilter]);
  const nodes = useMemo<Node[]>(() => filteredGraph.nodes.map((node, index) => ({ id: node.id, type: 'default', position: { x: (index % 4) * 260, y: Math.floor(index / 4) * 150 }, data: { label: `${node.label}\n${node.status}` }, className: `graph-node ${node.status}` })), [filteredGraph]);
  const edges = useMemo<Edge[]>(() => filteredGraph.edges.map((edge) => ({ id: edge.id, source: edge.source, target: edge.target, label: edge.relation, animated: edge.relation === 'produces' })), [filteredGraph]);
  return <main className="board-shell"><header className="board-header"><div><span className="eyebrow">OPEN SOURCE GRAPH CANVAS · REACT FLOW</span><h1>制作关系画布</h1><p>项目 → 分集 → 镜头 → 素材。筛选缺失/待处理节点，优先修复阻塞链路。</p></div><Link className="ghost-button" href="/">返回工作台</Link></header>{error ? <div className="alert">{error}</div> : <><div className="board-toolbar" aria-label="关系图筛选"><label>状态<select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as StatusFilter)}><option value="all">全部节点</option><option value="incomplete">缺失 / 待处理</option><option value="ready">已就绪</option></select></label><label>类型<select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value as TypeFilter)}><option value="all">全部类型</option><option value="story">故事资产</option><option value="production">制作链路</option><option value="asset">媒体资产</option></select></label><span className="muted">显示 {nodes.length} / {graph?.nodes.length ?? 0} 个节点 · {edges.length} 条关系</span></div><div className="board">{graph && nodes.length === 0 ? <div className="board-empty">当前筛选没有匹配节点，请切换状态或类型。</div> : <ReactFlow nodes={nodes} edges={edges} fitView><MiniMap /><Controls /><Background gap={24} size={1} /></ReactFlow>}</div></>}</main>;
}
