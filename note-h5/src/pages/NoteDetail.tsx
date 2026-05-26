import { useState, useEffect } from "react";
import { useParams } from "react-router-dom";
import { Button, Card, Space, Tag, Typography, Modal, Input, Select, Empty, message } from "antd";
import { EditOutlined, DeleteOutlined, PushpinOutlined, InboxOutlined, ArrowLeftOutlined } from "@ant-design/icons";
import { useNotes } from "@/hooks/useNotes";
import type { Note, NoteUpdate } from "@/types";

const { Title, Text } = Typography;

export function NoteDetail() {
  const { id } = useParams<{ id: string }>();
  const { notes, loading, error, fetchNotes, updateNote, deleteNote } = useNotes();

  const [note, setNote] = useState<Note | null>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [editContent, setEditContent] = useState("");
  const [editTitle, setEditTitle] = useState("");
  const [editTags, setEditTags] = useState<string[]>([]);
  const [deleteModalVisible, setDeleteModalVisible] = useState(false);

  useEffect(() => {
    fetchNotes();
  }, [fetchNotes]);

  useEffect(() => {
    if (id && notes.length > 0) {
      const found = notes.find(n => n.id === id);
      if (found) {
        setNote(found);
        setEditContent(found.content);
        setEditTitle(found.content?.substring(0, 30) || "");
        setEditTags(found.tags || []);
      }
    }
  }, [id, notes]);

  const handleSave = async () => {
    if (!note) return;
    try {
      const updates: NoteUpdate = {
        content: editContent,
        tags: editTags,
      };
      await updateNote(note.id, updates);
      setIsEditing(false);
      message.success("笔记已保存");
    } catch (err) {
      message.error("保存失败");
    }
  };

  const handleDelete = async () => {
    if (!note) return;
    try {
      await deleteNote(note.id);
      message.success("笔记已删除");
      setDeleteModalVisible(false);
    } catch (err) {
      message.error("删除失败");
    }
  };

  const handleTogglePin = async () => {
    if (!note) return;
    try {
      await updateNote(note.id, { isTop: !note.isTop });
      message.success(note.isTop ? "已取消置顶" : "已置顶");
    } catch (err) {
      message.error("操作失败");
    }
  };

  const handleToggleArchive = async () => {
    if (!note) return;
    try {
      await updateNote(note.id, { isArchive: !note.isArchive });
      message.success(note.isArchive ? "已取消归档" : "已归档");
    } catch (err) {
      message.error("操作失败");
    }
  };

  if (loading) {
    return (
      <div style={{ padding: 24, textAlign: "center" }}>
        <Text>加载中...</Text>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: 24, textAlign: "center" }}>
        <Text type="danger">错误: {error}</Text>
      </div>
    );
  }

  if (!note) {
    return (
      <div style={{ padding: 24, textAlign: "center" }}>
        <Empty description="笔记不存在" />
      </div>
    );
  }

  return (
    <div style={{ padding: 24, maxWidth: 800, margin: "0 auto" }}>
      <Space direction="vertical" style={{ width: "100%" }} size="large">
        <Space>
          <Button icon={<ArrowLeftOutlined />} onClick={() => window.history.back()}>
            返回
          </Button>
        </Space>

        <Card>
          {isEditing ? (
            <Space direction="vertical" style={{ width: "100%" }} size="middle">
              <Input
                value={editTitle}
                onChange={e => setEditTitle(e.target.value)}
                placeholder="笔记标题"
                size="large"
              />
              <Input.TextArea
                value={editContent}
                onChange={e => setEditContent(e.target.value)}
                rows={8}
                placeholder="笔记内容"
              />
              <Select
                mode="tags"
                value={editTags}
                onChange={setEditTags}
                placeholder="选择或输入标签"
                style={{ width: "100%" }}
                options={[]}
              />
              <Space>
                <Button type="primary" onClick={handleSave}>
                  保存
                </Button>
                <Button onClick={() => setIsEditing(false)}>
                  取消
                </Button>
              </Space>
            </Space>
          ) : (
            <Space direction="vertical" style={{ width: "100%" }} size="middle">
              <Title level={3}>{note.content?.substring(0, 50) || "无内容"}</Title>
              <div>{note.content}</div>
              <Space wrap>
                {note.tags?.map(tagName => (
                  <Tag key={tagName}>{tagName}</Tag>
                ))}
              </Space>
              <Space>
                <Text type="secondary">创建时间: {note.createTime}</Text>
                <Text type="secondary">更新时间: {note.updateTime}</Text>
              </Space>
              <Space>
                <Button
                  icon={<EditOutlined />}
                  onClick={() => setIsEditing(true)}
                >
                  编辑
                </Button>
                <Button
                  icon={<PushpinOutlined />}
                  type={note.isTop ? "primary" : "default"}
                  onClick={handleTogglePin}
                >
                  {note.isTop ? "已置顶" : "置顶"}
                </Button>
                <Button
                  icon={<InboxOutlined />}
                  type={note.isArchive ? "primary" : "default"}
                  onClick={handleToggleArchive}
                >
                  {note.isArchive ? "已归档" : "归档"}
                </Button>
                <Button
                  icon={<DeleteOutlined />}
                  danger
                  onClick={() => setDeleteModalVisible(true)}
                >
                  删除
                </Button>
              </Space>
            </Space>
          )}
        </Card>
      </Space>

      <Modal
        title="确认删除"
        open={deleteModalVisible}
        onOk={handleDelete}
        onCancel={() => setDeleteModalVisible(false)}
        okText="删除"
        cancelText="取消"
        okButtonProps={{ danger: true }}
      >
        <Text>确定要删除这篇笔记吗？此操作不可撤销。</Text>
      </Modal>
    </div>
  );
}