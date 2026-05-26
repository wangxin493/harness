import { ShareLink } from "@/types";

const mockShareLinks: ShareLink[] = [];

let nextId = 1;

function generateId(): string {
  return `share_${nextId++}_${Date.now()}`;
}

export const shareService = {
  async createShareLink(noteId: string, permission: "view" | "edit"): Promise<ShareLink> {
    const newLink: ShareLink = {
      id: generateId(),
      noteId,
      permission,
      url: `https://example.com/share/${generateId()}`,
      createdAt: new Date().toISOString(),
    };
    mockShareLinks.push(newLink);
    return newLink;
  },

  async getShareLinks(noteId: string): Promise<ShareLink[]> {
    return mockShareLinks.filter((link) => link.noteId === noteId);
  },

  async deleteShareLink(id: string): Promise<void> {
    const index = mockShareLinks.findIndex((link) => link.id === id);
    if (index !== -1) {
      mockShareLinks.splice(index, 1);
    }
  },
};
