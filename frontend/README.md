# Scenara Model Frontend

React + TypeScript 管理台源码。

## 本地运行

```powershell
npm install
npm run dev
```

默认代理到：

```text
http://127.0.0.1:8080
```

后端启动：

```powershell
python -m scenara_model.cli serve --host 127.0.0.1 --port 8080
```

## 生产构建

```powershell
npm run build
npm run preview
```

如需指定 API：

```powershell
$env:VITE_API_BASE_URL="http://127.0.0.1:8080"
npm run build
```

