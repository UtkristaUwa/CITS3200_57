import { Box, Typography, Button, Tooltip } from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import PictureAsPdfIcon from '@mui/icons-material/PictureAsPdf';
import DescriptionIcon from '@mui/icons-material/Description';
import TableChartIcon from '@mui/icons-material/TableChart';
import FolderZipIcon from '@mui/icons-material/FolderZip';
import InsertDriveFileIcon from '@mui/icons-material/InsertDriveFile';
import type { TenderDocument } from '../lib/api';

function FileIcon({ fileType }: { fileType: string | null }) {
  const ext = (fileType ?? '').toLowerCase();
  if (ext === 'pdf') return <PictureAsPdfIcon sx={{ fontSize: 20, color: '#d32f2f' }} />;
  if (ext === 'doc' || ext === 'docx' || ext === 'rtf') return <DescriptionIcon sx={{ fontSize: 20, color: '#1976d2' }} />;
  if (ext === 'xls' || ext === 'xlsx' || ext === 'csv') return <TableChartIcon sx={{ fontSize: 20, color: '#388e3c' }} />;
  if (ext === 'zip' || ext === 'tar' || ext === 'gz') return <FolderZipIcon sx={{ fontSize: 20, color: '#f57c00' }} />;
  return <InsertDriveFileIcon sx={{ fontSize: 20, color: '#757575' }} />;
}

async function triggerDownload(url: string, fileName: string) {
  try {
    const res = await fetch(url);
    const blob = await res.blob();
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(blobUrl);
  } catch {
    window.open(url, '_blank', 'noopener,noreferrer');
  }
}

function openInline(url: string) {
  const inlineUrl = url.includes('?')
    ? `${url}&response-content-disposition=inline`
    : `${url}?response-content-disposition=inline`;
  window.open(inlineUrl, '_blank', 'noopener,noreferrer');
}

export function TenderDocuments({ documents }: { documents: TenderDocument[] }) {
  const visible = documents.filter(d => !d.file_name.startsWith('__tender__'));
  if (visible.length === 0) return null;

  return (
    <Box sx={{ mt: 2 }}>
      <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1 }}>
        Attachments ({visible.length})
      </Typography>
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
        {visible.map((doc, i) => {
          const available = !!doc.storage_url;
          const isPdf = (doc.file_type ?? '').toLowerCase() === 'pdf';
          return (
            <Box
              key={doc.document_id ?? i}
              sx={{
                display: 'flex',
                alignItems: 'center',
                gap: 1,
                p: 1,
                borderRadius: 1,
                border: '1px solid',
                borderColor: 'divider',
                bgcolor: 'background.paper',
                minWidth: 0,
              }}
            >
              <FileIcon fileType={doc.file_type} />
              <Tooltip title={doc.file_name} placement="top">
                <Typography
                  variant="body2"
                  sx={{
                    flex: 1,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    minWidth: 0,
                  }}
                >
                  {doc.file_name}
                </Typography>
              </Tooltip>
              <Box sx={{ display: 'flex', gap: 0.5, flexShrink: 0 }}>
                <Tooltip title={available ? 'Download' : 'File not yet available'}>
                  <span>
                    <Button
                      size="small"
                      variant="outlined"
                      startIcon={<DownloadIcon />}
                      disabled={!available}
                      onClick={() => available && triggerDownload(doc.storage_url!, doc.file_name)}
                      sx={{ minHeight: 32, textTransform: 'none', fontSize: '0.75rem' }}
                    >
                      Download
                    </Button>
                  </span>
                </Tooltip>
                {isPdf && (
                  <Tooltip title={available ? 'View PDF' : 'File not yet available'}>
                    <span>
                      <Button
                        size="small"
                        variant="outlined"
                        startIcon={<OpenInNewIcon />}
                        disabled={!available}
                        onClick={() => available && openInline(doc.storage_url!)}
                        sx={{ minHeight: 32, textTransform: 'none', fontSize: '0.75rem' }}
                      >
                        View
                      </Button>
                    </span>
                  </Tooltip>
                )}
              </Box>
            </Box>
          );
        })}
      </Box>
    </Box>
  );
}
