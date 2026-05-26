import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Button, Card, Space, Tag, Typography, Input, Form, message, Select } from "antd";
import { ArrowLeftOutlined } from "@ant-design/icons";
import { useNotes } from "@/hooks/useNotes";
import { useTags } from "@/hooks/useTags";
import type { Note, Tag as TagType } from "@/types";

const { TextArea } = Input;
const { Title } = Typography;

export function NoteEditor() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { notes, updateNote } = useNotes();
  const { tags } = useTags();
  
  const [content, setContent] = useState("");
  const [selectedTagIds, setSelectedTagIds] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (id && notes.length > 0) {
      const note = notes.find((n: Note) => n.id === id);
      if (note) {
        setContent(note.content);
        setSelectedTagIds(note.tags || []);
      }
    }
  }, [id, notes]);

  const handleSave = async () => {
    if (!id) return;
    try {
      setSaving(true);
      await updateNote(id, {
        content,
        tags: selectedTagIds,
      });
      message.success("笔记保存成功");
      navigate(-1);
    } catch (err) {
      message.error(err instanceof Error ? err.message : "保存笔记失败");
    } finally {
      setSaving(false);
    }
  };

  const handleBack = () => {
    navigate(-1);
  };

  const tagOptions = tags.map((tag: TagType) => ({
    label: tag.name,
    value: tag.id,
  }));

  return (
    <div style={{ padding: 24 }}>
      <Space direction="vertical" style={{ width: "100%" }} size="large">
        <Space>
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={handleBack}
          >
            返回
          </Button>
          <Title level={4} style={{ margin: 0 }}>
            {id ? "编辑笔记" : "新建笔记"}
          </Title>
        </Space>

        <Card>
          <Form layout="vertical">
            <Form.Item label="内容">
              <TextArea
                rows={8}
                value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder="请输入笔记内容..."
              />
            </Form.Item>

            <Form.Item label="标签">
              <Select
                mode="multiple"
                placeholder="选择标签"
                value={selectedTagIds}
                onChange={setSelectedTagIds}
                options={tagOptions}
                style={{ width: "100%" }}
              />
              {selectedTagIds.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  {selectedTagIds.map((tagId) => {
                    const tag = tags.find((t: TagType) => t.id === tagId);
                    return tag ? (
                      <Tag key={tagId} color={tag.color || "blue"} style={{ marginBottom: 4 }}>
                        {tag.name}
                      </Tag>
                    ) : null;
                  })}
                </div>
              )}
            </Form.Item>

            <Form.Item>
              <Space>
                <Button type="primary" onClick={handleSave} loading={saving}>
                  保存
                </Button>
                <Button onClick={handleBack}>取消</Button>
              </Space>
            </Form.Item>
          </Form>
        </Card>
      </Space>
    </div>
  );
}