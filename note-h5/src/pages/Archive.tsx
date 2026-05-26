import { useEffect, useState } from "react";
import { useNotes } from "@/hooks/useNotes";
import { Note } from "@/types";
import { Button, Card, Space, Tag, Typography, Empty, message, Modal } from "antd";
import { DeleteOutlined, InboxOutlined, ArrowLeftOutlined } from "@ant-design/icons";

const { Paragraph } = Typography;

export function Archive() {
  const { notes, loading, error, fetchNotes, updateNote, deleteNote } = useNotes();
  const [archivedNotes, setArchivedNotes] = useState<Note[]>([]);

  useEffect(() => {
    fetchNotes();
  }, [fetchNotes]);

  useEffect(() => {
    setArchivedNotes(notes.filter((note) => note.isArchive));
  }, [notes]);

  const handleRestore = async (note: Note) => {
    try {
      await updateNote(note.id, { isArchive: false });
      message.success("笔记已恢复");
    } catch {
      message.error("恢复失败");
    }
  };

  const handleDelete = (note: Note) => {
    Modal.confirm({
      title: "确认删除",
      content: "删除后无法恢复，确定要删除吗？",
      okText: "删除",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await deleteNote(note.id);
          message.success("笔记已删除");
        } catch {
          message.error("删除失败");
        }
      },
    });
  };

  if (loading) {
    return <div style={{ padding: 24, textAlign: "center" }}>加载中...</div>;
  }

  if (error) {
    return <div style={{ padding: 24, textAlign: "center", color: "red" }}>错误: {error}</div>;
  }

  if (archivedNotes.length === 0) {
    return (
      <div style={{ padding: 24 }}>
        <Empty
          image={<InboxOutlined style={{ fontSize: 64, color: "#d9d9d9" }} />}
          description="暂无归档笔记"
        />
      </div>
    );
  }

  return (
    <div style={{ padding: 24 }}>
      <Typography.Title level={4} style={{ marginBottom: 16 }}>
        归档笔记 ({archivedNotes.length})
      </Typography.Title>
      <Space direction="vertical" style={{ width: "100%" }}>
        {archivedNotes.map((note) => (
          <Card key={note.id} size="small">
            <Paragraph ellipsis={{ rows: 2 }}>{note.content}</Paragraph>
            <div style={{ marginBottom: 8 }}>
              {note.tags.map((tag) => (
                <Tag key={tag}>{tag}</Tag>
              ))}
            </div>
            <Space>
              <Button
                type="link"
                icon={<ArrowLeftOutlined />}
                onClick={() => handleRestore(note)}
              >
                恢复
              </Button>
              <Button
                type="link"
                danger
                icon={<DeleteOutlined />}
                onClick={() => handleDelete(note)}
              >
                删除
              </Button>
            </Space>
          </Card>
        ))}
      </Space>
    </div>
  );
}