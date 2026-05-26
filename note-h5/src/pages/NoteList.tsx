import { useEffect, useState } from "react";
import { useNotes } from "@/hooks/useNotes";
import { Note } from "@/types";
import { Button, Card, Space, Tag as AntTag, Typography, Empty, message, Modal } from "antd";
import { PushpinOutlined, DeleteOutlined, InboxOutlined } from "@ant-design/icons";

const { Paragraph, Text } = Typography;

export function NoteList() {
  const { notes, loading, error, fetchNotes, createNote, updateNote, deleteNote } = useNotes();
  const [deleteModalVisible, setDeleteModalVisible] = useState(false);
  const [noteToDelete, setNoteToDelete] = useState<string | null>(null);

  useEffect(() => {
    fetchNotes();
  }, [fetchNotes]);

  const handleTogglePin = async (note: Note) => {
    try {
      await updateNote(note.id, { isTop: !note.isTop });
      message.success(note.isTop ? "已取消置顶" : "已置顶");
    } catch {
      message.error("操作失败");
    }
  };

  const handleArchive = async (note: Note) => {
    try {
      await updateNote(note.id, { isArchive: !note.isArchive });
      message.success(note.isArchive ? "已取消归档" : "已归档");
    } catch {
      message.error("操作失败");
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteNote(id);
      message.success("已删除");
      setDeleteModalVisible(false);
      setNoteToDelete(null);
    } catch {
      message.error("删除失败");
    }
  };

  const showDeleteConfirm = (id: string) => {
    setNoteToDelete(id);
    setDeleteModalVisible(true);
  };

  const sortedNotes = [...notes].sort((a, b) => {
    if (a.isTop && !b.isTop) return -1;
    if (!a.isTop && b.isTop) return 1;
    return new Date(b.createTime).getTime() - new Date(a.createTime).getTime();
  });

  if (loading) {
    return <div style={{ textAlign: "center", padding: 40 }}>加载中...</div>;
  }

  if (error) {
    return <div style={{ textAlign: "center", padding: 40, color: "red" }}>错误: {error}</div>;
  }

  if (notes.length === 0) {
    return <Empty description="暂无笔记" />;
  }

  return (
    <div style={{ padding: 24 }}>
      <Space direction="vertical" style={{ width: "100%" }} size="middle">
        {sortedNotes.map((note) => (
          <Card
            key={note.id}
            size="small"
            title={
              <Space>
                {note.isTop && <PushpinOutlined style={{ color: "#faad14" }} />}
                <Text strong>{note.content?.substring(0, 50) || "无内容"}</Text>
              </Space>
            }
            extra={
              <Space>
                <Button
                  type="text"
                  icon={<PushpinOutlined />}
                  onClick={() => handleTogglePin(note)}
                  title={note.isTop ? "取消置顶" : "置顶"}
                />
                <Button
                  type="text"
                  icon={<InboxOutlined />}
                  onClick={() => handleArchive(note)}
                  title={note.isArchive ? "取消归档" : "归档"}
                />
                <Button
                  type="text"
                  danger
                  icon={<DeleteOutlined />}
                  onClick={() => showDeleteConfirm(note.id)}
                  title="删除"
                />
              </Space>
            }
          >
            <Paragraph ellipsis={{ rows: 2 }}>{note.content}</Paragraph>
            <Space>
              {note.tags.map((tag) => (
                <AntTag key={tag}>{tag}</AntTag>
              ))}
            </Space>
            <div style={{ marginTop: 8 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {new Date(note.createTime).toLocaleString()}
              </Text>
            </div>
          </Card>
        ))}
      </Space>
      <Modal
        title="确认删除"
        open={deleteModalVisible}
        onOk={() => noteToDelete && handleDelete(noteToDelete)}
        onCancel={() => {
          setDeleteModalVisible(false);
          setNoteToDelete(null);
        }}
        okText="删除"
        cancelText="取消"
        okButtonProps={{ danger: true }}
      >
        <p>确定要删除这条笔记吗？此操作不可恢复。</p>
      </Modal>
    </div>
  );
}