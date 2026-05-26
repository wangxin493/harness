import { Note, NoteInput, NoteUpdate } from "@/types";

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

let notes: Note[] = [
  {
    id: "1",
    content: "欢迎使用笔记应用！",
    isTop: false,
    isArchive: false,
    tags: ["入门"],
    createTime: new Date().toISOString(),
    updateTime: new Date().toISOString(),
  },
];

export const noteService = {
  async getNotes(): Promise<Note[]> {
    await delay(300);
    return notes.filter((n) => !n.isArchive);
  },

  async createNote(input: NoteInput): Promise<Note> {
    await delay(300);
    const newNote: Note = {
      id: `note_${Date.now()}`,
      content: input.content,
      isTop: false,
      isArchive: false,
      tags: input.tags || [],
      createTime: new Date().toISOString(),
      updateTime: new Date().toISOString(),
    };
    notes.push(newNote);
    return newNote;
  },

  async updateNote(id: string, updates: NoteUpdate): Promise<Note> {
    await delay(300);
    const index = notes.findIndex((n) => n.id === id);
    if (index === -1) throw new Error("笔记不存在");
    notes[index] = {
      ...notes[index],
      ...updates,
      updateTime: new Date().toISOString(),
    };
    return notes[index];
  },

  async deleteNote(id: string): Promise<void> {
    await delay(300);
    notes = notes.filter((n) => n.id !== id);
  },

  async togglePin(id: string): Promise<Note> {
    const note = notes.find((n) => n.id === id);
    if (!note) throw new Error("笔记不存在");
    return this.updateNote(id, { isTop: !note.isTop });
  },

  async archiveNote(id: string): Promise<Note> {
    return this.updateNote(id, { isArchive: true });
  },

  async restoreNote(id: string): Promise<Note> {
    return this.updateNote(id, { isArchive: false });
  },
};
