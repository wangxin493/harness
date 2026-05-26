import { useState, useCallback } from "react";
import { Tag } from "@/types";
import { tagService } from "@/api/tagService";

export function useTags() {
  const [tags, setTags] = useState<Tag[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchTags = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await tagService.getTags();
      setTags(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "获取标签失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const createTag = useCallback(async (name: string, color?: string) => {
    try {
      setError(null);
      const newTag = await tagService.createTag({ name, color });
      setTags(prev => [...prev, newTag]);
      return newTag;
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建标签失败");
      throw err;
    }
  }, []);

  const updateTag = useCallback(async (id: string, updates: Partial<Tag>) => {
    try {
      setError(null);
      const updatedTag = await tagService.updateTag(id, updates);
      setTags(prev => prev.map(tag => tag.id === id ? updatedTag : tag));
      return updatedTag;
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新标签失败");
      throw err;
    }
  }, []);

  const deleteTag = useCallback(async (id: string) => {
    try {
      setError(null);
      await tagService.deleteTag(id);
      setTags(prev => prev.filter(tag => tag.id !== id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除标签失败");
      throw err;
    }
  }, []);

  const getTags = useCallback(async () => {
    await fetchTags();
    return tags;
  }, [fetchTags, tags]);

  return { tags, loading, error, fetchTags, createTag, updateTag, deleteTag, getTags };
}