import { useState } from 'react';
import {
  Box,
  Typography,
  IconButton,
  Tooltip,
  CircularProgress,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import PictureAsPdfIcon from '@mui/icons-material/PictureAsPdf';
import DescriptionIcon from '@mui/icons-material/Description';
import TableChartIcon from '@mui/icons-material/TableChart';
import FolderZipIcon from '@mui/icons-material/FolderZip';
import InsertDriveFileIcon from '@mui/icons-material/InsertDriveFile';
import type { TenderDocument } from '../lib/api';
import { getDocumentBlob } from '../lib/api';

function fileExtension(fileName: string): string {
  return fileName.split('.').pop()?.toLowerCase() ?? '';
}

function FileTypeIcon({ doc }: { doc: TenderDocument }) {
  const ext = fileExtension(doc.file_name);
  if (doc.file_type === 'pdf' || ext === 'pdf') {
    return <PictureAsPdfIcon sx={{ fontSize: 18, color: '#c62828', flexShrink: 0 }} aria-hidden />;
  }
  if (doc.file_type === 'docx' || ext === 'docx' || ext === 'doc') {
    return <DescriptionIcon sx={{ fontSize: 18, color: '#1565c0', flexShrink: 0 }} aria-hidden />;
  }
  if (ext === 'xlsx' || ext === 'xls') {
    return <TableChartIcon sx={{ fontSize: 18, color: '#2e7d32', flexShrink: 0 }} aria-hidden />;
  }
  if (ext === 'zip' || ext === 'gz' || ext === '7z') {
    return <FolderZipIcon sx={{ fontSize: 18, color: '#e65100', flexShrink: 0 }} aria-hidden />;
  }
  return <InsertDriveFileIcon sx={{ fontSize: 18, color: 'text.disabled', flexShrink: 0 }} aria-hidden />;
}

function canViewInline(doc: TenderDocument): boolean {
  const ext = fileExtension(doc.file_name);
  return doc.file_type === 'pdf' || ext === 'pdf';
}

async function triggerDownload(url: string, fileName: string): Promise<void> {
  const blob = await getDocumentBlob(url, fileName);
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = objectUrl;
  a.download = fileName;
  a.click();
  URL.revokeObjectURL(objectUrl);
}

async function openInline(url: string, fileName: string): Promise<void> {
  const blob = await getDocumentBlob(url, fileName);
  const objectUrl = URL.createObjectURL(blob);
  window.open(objectUrl, '_blank', 'noopener,noreferrer');
}

function DocumentRow({ doc }: { doc: TenderDocument }) {
  const [downloading, setDownloading] = useState(false);
  const hasUrl = Boolean(doc.storage_url);
  const viewable = canViewInline(doc);

  async function handleDownload() {
    if (!doc.storage_url) return;
    setDownloading(true);
    try {
      await triggerDownload(doc.storage_url, doc.file_name);
    } finally {
      setDownloading(false);
    }
  }

  return (
    <Box
      component="li"
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 1,
        py: 0.75,
        borderBottom: '1px solid',
        borderColor: 'divider',
        '&:last-child': { borderBottom: 'none' },
        minWidth: 0,
      }}
    >
      <FileTypeIcon doc={doc} />

      <Tooltip title={doc.file_name} placement="top" enterDelay={600}>
        <Typography
          variant="body2"
          sx={{
            flex: 1,
            minWidth: 0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            color: 'text.primary',
          }}
        >
          {doc.file_name}
        </Typography>
      </Tooltip>

      <Box sx={{ display: 'flex', gap: 0.5, flexShrink: 0 }}>
        <Tooltip title={hasUrl ? `Download ${doc.file_name}` : 'File not yet available'}>
          <span>
            <IconButton
              size="small"
              onClick={handleDownload}
              disabled={!hasUrl || downloading}
              aria-label={`Download ${doc.file_name}`}
              sx={{ minWidth: 36, minHeight: 36 }}
            >
              {downloading ? (
                <CircularProgress size={16} color="inherit" />
              ) : (
                <DownloadIcon sx={{ fontSize: 18 }} />
              )}
            </IconButton>
          </span>
        </Tooltip>

        {viewable && (
          <Tooltip title={hasUrl ? `View ${doc.file_name}` : 'File not yet available'}>
            <span>
              <IconButton
                size="small"
                onClick={() => doc.storage_url && openInline(doc.storage_url, doc.file_name)}
                disabled={!hasUrl}
                aria-label={`View ${doc.file_name} in browser`}
                sx={{ minWidth: 36, minHeight: 36 }}
              >
                <OpenInNewIcon sx={{ fontSize: 18 }} />
              </IconButton>
            </span>
          </Tooltip>
        )}
      </Box>
    </Box>
  );
}

export function TenderDocuments({ documents }: { documents: TenderDocument[] }) {
  const visible = documents.filter(d =>
    !d.file_name.startsWith('__tender__') && !d.file_name.endsWith('.txt')
  );

  return (
    <Box sx={{ mt: 2 }}>
      <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1 }}>
        Documents
      </Typography>

      {visible.length === 0 ? (
        <Typography variant="body2" color="text.secondary" sx={{ fontStyle: 'italic' }}>
          No documents available.
        </Typography>
      ) : (
        <Box
          component="ul"
          sx={{
            listStyle: 'none',
            m: 0,
            p: 0,
            border: '1px solid',
            borderColor: 'divider',
            borderRadius: 1,
            px: 1.5,
          }}
        >
          {visible.map((doc, i) => (
            <DocumentRow key={doc.document_id ?? `${doc.file_name}-${i}`} doc={doc} />
          ))}
        </Box>
      )}
    </Box>
  );
}
