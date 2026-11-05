// Contract of /api/search and /api/browse (src/footage/archive.py: serialize()).
export interface ArchiveResult {
  keyframe_id: number;
  video_id: string;
  pts_time: number;
  frame_idx: number | null;
  rank: number;
  score: number | null;
  evidence: string | null;
  image_url: string;
  detail_url: string;
  title: string | null;
  collection: string | null;
  owner: string | null;
  access_level: string | null;
  topics: string[];
}

export interface ArchivePayload {
  mode: "search" | "browse";
  query: string;
  top_k?: number;
  sources?: string[];
  latency_ms?: number;
  notes?: string[];
  filters: { collection: number | null; topic: number | null };
  results: ArchiveResult[];
}

export type ViewName = "ring" | "corridor" | "constellation";

export interface BootUser {
  name: string;
  username: string;
  roles: string[];
  permissions: string[];
}

export interface Boot {
  label: string;
  user: BootUser | null;
  links: Record<string, string | null>;
  view: ViewName;
  query: string;
  k: number;
  top_k_choices: number[];
  sources: { code: string; label: string; checked: boolean }[];
  visual: string;
  filters: { collection: number | null; topic: number | null };
  options: {
    topics: { id: number; name: string; parent: number | null; depth: number }[];
    collections: { id: number; title: string; owner: string; videos: number }[];
  };
  request_id: string;
  requests: { id: string; text: string; status: string; verified: boolean }[];
  initial: ArchivePayload | null;
  error: string | null;
}

export type SearchStatus = "anonymous" | "idle" | "loading" | "success" | "empty" | "error";
