import { NextRequest, NextResponse } from 'next/server';
import fs from 'node:fs';
import path from 'node:path';
import { Readable } from 'node:stream';

const ROOTS: Record<string, string> = {
  matrix: '/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-hd-matrix',
  longvideo: '/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-longvideo',
  'motion-context': '/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-motion-context',
  director: '/Volumes/AJW-Data/Projects/novel-to-comic-engine/test-runs/2026-08-13-h3-director-aimixer',
};

const CONTENT_TYPES: Record<string, string> = {
  '.json': 'application/json; charset=utf-8',
  '.mp4': 'video/mp4',
  '.png': 'image/png',
};

function resolveMediaPath(slug: string[]) {
  if (slug[0] !== 'media') return null;
  const rootName = ROOTS[slug[1]] ? slug[1] : 'matrix';
  const rootPath = ROOTS[rootName];
  const relativeParts = ROOTS[slug[1]] ? slug.slice(2) : slug.slice(1);
  const candidate = path.resolve(rootPath, relativeParts.join('/'));
  const root = `${path.resolve(rootPath)}${path.sep}`;
  if (!candidate.startsWith(root)) return null;
  return candidate;
}

function nodeStream(stream: fs.ReadStream) {
  return Readable.toWeb(stream) as unknown as ReadableStream;
}

export async function GET(request: NextRequest, { params }: { params: Promise<{ slug: string[] }> }) {
  const { slug } = await params;
  const filePath = resolveMediaPath(slug);
  if (!filePath || !fs.existsSync(filePath)) {
    return NextResponse.json({ error: 'Media not found' }, { status: 404 });
  }

  const stat = fs.statSync(filePath);
  if (!stat.isFile()) return NextResponse.json({ error: 'Media not found' }, { status: 404 });

  const extension = path.extname(filePath).toLowerCase();
  const headers = new Headers({
    'Accept-Ranges': 'bytes',
    'Cache-Control': 'public, max-age=300',
    'Content-Type': CONTENT_TYPES[extension] ?? 'application/octet-stream',
    'Last-Modified': stat.mtime.toUTCString(),
  });
  const range = request.headers.get('range');

  if (range && extension === '.mp4') {
    const match = /^bytes=(\d*)-(\d*)$/.exec(range);
    if (match) {
      const start = match[1] ? Number(match[1]) : Math.max(0, stat.size - Number(match[2] || 1));
      const requestedEnd = match[2] ? Number(match[2]) : stat.size - 1;
      const end = Math.min(requestedEnd, stat.size - 1);
      if (Number.isFinite(start) && Number.isFinite(end) && start <= end && start < stat.size) {
        headers.set('Content-Range', `bytes ${start}-${end}/${stat.size}`);
        headers.set('Content-Length', String(end - start + 1));
        return new NextResponse(nodeStream(fs.createReadStream(filePath, { start, end })), { status: 206, headers });
      }
    }
    return new NextResponse(null, { status: 416, headers: { 'Content-Range': `bytes */${stat.size}` } });
  }

  headers.set('Content-Length', String(stat.size));
  return new NextResponse(nodeStream(fs.createReadStream(filePath)), { headers });
}
