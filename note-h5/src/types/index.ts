export interface Note {
  id: string;
  content: string;
  isTop: boolean;
  isArchive: boolean;
  tags: string[];
  createTime: string;
  updateTime: string;
}

export interface NoteInput {
  content: string;
  title?: string;
  tags?: string[];
}

export interface NoteUpdate {
  content?: string;
  isTop?: boolean;
  isArchive?: boolean;
  tags?: string[];
}

export interface Tag {
  id: string;
  name: string;
  color?: string;
}

export interface ShareLink {
  id: string;
  noteId: string;
  permission: 'view' | 'edit';
  url: string;
  createdAt: string;
}