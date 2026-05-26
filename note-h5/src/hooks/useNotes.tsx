import { useState, useCallback } from "react";
import { Note, NoteInput, NoteUpdate } from "@/types";
import { noteService } from "@/api/noteService";

export function useNotes() {
  const [notes, setNotes] = useState<Note[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchNotes = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await noteService.getNotes();
      setNotes(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "获取笔记失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const createNote = useCallback(async (input: NoteInput) => {
    try {
      setError(null);
      const newNote = await noteService.createNote(input);
      setNotes(prev => [...prev, newNote]);
      return newNote;
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建笔记失败");
      throw err;
    }
  }, []);

  const updateNote = useCallback(async (id: string, updates: NoteUpdate) => {
    try {
      setError(null);
      const updatedNote = await noteService.updateNote(id, updates);
      setNotes(prev => prev.map(n => n.id === id ? updatedNote : n));
      return updatedNote;
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新笔记失败");
      throw err;
    }
  }, []);

  const deleteNote = useCallback(async (id: string) => {
    try {
      setError(null);
      await noteService.deleteNote(id);
      setNotes(prev => prev.filter(n => n.id !== id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除笔记失败");
      throw err;
    }
  }, []);

  const togglePin = useCallback(async (id: string) => {
    const note = notes.find(n => n.id === id);
    if (!note) return;
    try {
      const updatedNote = await noteService.updateNote(id, { isTop: !note.isTop });
      setNotes(prev => prev.map(n => n.id === id ? updatedNote : n));
    } catch (err) {
      setError(err instanceof Error ? err.message : "置顶操作失败");
      throw err;
    }
  }, [notes]);

  const archiveNote = useCallback(async (id: string) => {
    try {
      const updatedNote = await noteService.updateNote(id, { isArchive: true });
      setNotes(prev => prev.map(n => n.id === id ? updatedNote : n));
    } catch (err) {
      setError(err instanceof Error ? err.message : "归档操作失败");
      throw err;
    }
  }, []);

  const restoreNote = useCallback(async (id: string) => {
    try {
      const updatedNote = await noteService.updateNote(id, { isArchive: false });
      setNotes(prev => prev.map(n => n.id === id ? updatedNote : n));
    } catch (err) {
      setError(err instanceof Error ? err.message : "恢复操作失败");
      throw err;
    }
  }, []);

  return { notes, loading, error, fetchNotes, createNote, updateNote, deleteNote, togglePin, archiveNote, restoreNote };
}