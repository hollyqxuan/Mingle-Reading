## 上传导入

Mingle Reading 现在支持 `.txt`、`.pdf` 和 `.epub` 格式的本地导入，无需使用外部服务。

### 支持格式

- `txt`
  - 以 UTF-8 文本解码
- `pdf`
  - 使用 `pypdf` 本地解析
  - 提取的页面文本经归一化后送入现有的 `build_book_record` 流水线
- `epub`
  - 使用 zip/XML 读取器本地解析
  - 遵循 spine 顺序，确保各章节 XHTML 文件按阅读顺序读取

### 导入流程

1. `POST /api/upload` 接收文件
2. `backend/data/ingest/parser.py` 检测后缀并在本地提取可读文本
3. 提取的文本被归一化
4. 现有 `build_book_record(...)` 流程构建章节和 chunk
5. 现有时间图构建器基于生成的 `BookRecord` 运行

### 当前限制

- `pdf` 提取质量取决于 PDF 是否包含可选文本
- 不含 OCR 文本层的扫描版 PDF 暂不支持
- `epub` 支持目前聚焦于基于标准 spine 的 XHTML 内容
