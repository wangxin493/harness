import React from "react";
import { BrowserRouter, Routes, Route, Link } from "react-router-dom";
import { Layout, Menu } from "antd";
import { InboxOutlined, PushpinOutlined } from "@ant-design/icons";
import { NoteList } from "@/pages/NoteList";
import { Archive } from "@/pages/Archive";

const { Header, Content } = Layout;

const menuItems = [
  { key: "/", icon: <InboxOutlined />, label: <Link to="/">笔记</Link> },
  { key: "/archive", icon: <PushpinOutlined />, label: <Link to="/archive">归档</Link> },
];

const App: React.FC = () => {
  return (
    <BrowserRouter>
      <Layout style={{ minHeight: "100vh" }}>
        <Header>
          <Menu theme="dark" mode="horizontal" items={menuItems} />
        </Header>
        <Content style={{ padding: "24px" }}>
          <Routes>
            <Route path="/" element={<NoteList />} />
            <Route path="/archive" element={<Archive />} />
          </Routes>
        </Content>
      </Layout>
    </BrowserRouter>
  );
};

export { App };