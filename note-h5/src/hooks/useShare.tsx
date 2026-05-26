import { useState, useCallback } from 'react';
import { ShareLink } from '@/types';
import { shareService } from '@/api/shareService';

export function useShare() {
  const [shareLinks, setShareLinks] = useState<ShareLink[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const createShareLink = useCallback(async (noteId: string, permission: 'view' | 'edit') => {
    try {
      setLoading(true);
      setError(null);
      const newLink = await shareService.createShareLink(noteId, permission);
      setShareLinks(prev => [...prev, newLink]);
      return newLink;
    } catch (err) {
      const message = err instanceof Error ? err.message : '创建分享链接失败';
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  const getShareLinks = useCallback(async (noteId: string) => {
    try {
      setLoading(true);
      setError(null);
      const links = await shareService.getShareLinks(noteId);
      setShareLinks(links);
      return links;
    } catch (err) {
      const message = err instanceof Error ? err.message : '获取分享链接失败';
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  const deleteShareLink = useCallback(async (id: string) => {
    try {
      setLoading(true);
      setError(null);
      await shareService.deleteShareLink(id);
      setShareLinks(prev => prev.filter(link => link.id !== id));
    } catch (err) {
      const message = err instanceof Error ? err.message : '删除分享链接失败';
      setError(message);
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  return { shareLinks, loading, error, createShareLink, getShareLinks, deleteShareLink };
}