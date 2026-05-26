import { useState, useEffect } from "react";
import { useTags } from "@/hooks/useTags";
import type { Tag } from "@/types";
import { Button, Input, Space, Tag as AntTag, message, Card, Empty, Typography } from "antd";
import { PlusOutlined, DeleteOutlined } from "@ant-design/icons";

const { Text } = Typography;

export function TagManager() {
  const { tags, loading, error, fetchTags, createTag, deleteTag } = useTags();
  const [newTagName, setNewTagName] = useState("");
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    fetchTags();
  }, [fetchTags]);

  const handleCreate = async () => {
    const name = newTagName.trim();
    if (!name) {
      message.warning("请输入标签名称");
      return;
    }
    if (tags.some((tag) => tag.name === name)) {
      message.warning("标签已存在");
      return;
    }
    setCreating(true);
    try {
      await createTag(name);
      setNewTagName("");
      message.success("标签创建成功");
    } catch {
      message.error("创建标签失败");
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (tag: Tag) => {
    try {
      await deleteTag(tag.id);
      message.success("标签已删除");
    } catch {
      message.error("删除标签失败");
    }
  };

  if (error) {
    return <div style={{ padding: 24, textAlign: "center" }}>错误: {error}</div>;
  }

  return (
    <Card title="标签管理" style={{ margin: 24 }}>
      <Space direction="vertical" style={{ width: "100%" }}>
        <Space>
          <Input
            placeholder="输入新标签名称"
            value={newTagName}
            onChange={(e) => setNewTagName(e.target.value)}
            onPressEnter={handleCreate}
            style={{ width: 200 }}
          />
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={handleCreate}
            loading={creating}
          >
            创建标签
          </Button>
        </Space>
        {loading ? (
          <div>加载中...</div>
        ) : tags.length === 0 ? (
          <Empty description="暂无标签" />
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {tags.map((tag) => (
              <Space key={tag.id}>
                <AntTag color={tag.color || "blue"}>{tag.name}</AntTag>
                <Button
                  type="link"
                  danger
                  icon={<DeleteOutlined />}
                  onClick={() => handleDelete(tag)}
                  size="small"
                />
              </Space>
            ))}
          </div>
        )}
      </Space>
    </Card>
  );
}