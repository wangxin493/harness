import { Tag } from "@/types";

const STORAGE_KEY = "tags";

function getStoredTags(): Tag[] {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored ? JSON.parse(stored) : [];
}

function saveTags(tags: Tag[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(tags));
}

export const tagService = {
  async getTags(): Promise<Tag[]> {
    return getStoredTags();
  },

  async createTag(tag: Omit<Tag, "id">): Promise<Tag> {
    const tags = getStoredTags();
    const newTag: Tag = {
      id: `tag_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      ...tag,
    };
    tags.push(newTag);
    saveTags(tags);
    return newTag;
  },

  async updateTag(id: string, updates: Partial<Tag>): Promise<Tag> {
    const tags = getStoredTags();
    const index = tags.findIndex((t) => t.id === id);
    if (index === -1) {
      throw new Error(`Tag with id ${id} not found`);
    }
    const updatedTag: Tag = { ...tags[index], ...updates };
    tags[index] = updatedTag;
    saveTags(tags);
    return updatedTag;
  },

  async deleteTag(id: string): Promise<void> {
    const tags = getStoredTags();
    const filtered = tags.filter((t) => t.id !== id);
    if (filtered.length === tags.length) {
      throw new Error(`Tag with id ${id} not found`);
    }
    saveTags(filtered);
  },
};
