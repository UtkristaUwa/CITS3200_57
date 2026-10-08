import { useId, useMemo, useState } from 'react';
import {
  Box,
  Typography,
  IconButton,
  Tooltip,
  CircularProgress,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
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

const BRAND_ORANGE = '#FF7C00';

function fileExtension(fileName: string): string {
  return fileName.split('.').pop()?.toLowerCase() ?? '';
}

function deriveDisplayName(doc: TenderDocument): string {
  if (!doc.storage_url) return doc.file_name;
  try {
    const pathPart = new URL(doc.storage_url).pathname.split('/').pop() ?? '';
    if (!pathPart) return doc.file_name;
    const decoded = decodeURIComponent(pathPart);
    // Strip the 16-char hex hash prefix: "f239d7a8b595541c-filename.pdf" → "filename.pdf"
    return decoded.replace(/^[0-9a-f]{16}-/i, '');
  } catch {
    return doc.file_name;
  }
}

// Short, scannable type label ("PDF", "XLSX"). The filename extension is
// preferred over file_type because file_type is the schema's coarse enum
// (pdf/docx/rtf/other) and would label spreadsheets and zips as "OTHER".
function fileTypeLabel(doc: TenderDocument, displayName: string): string {
  const ext = displayName.includes('.') ? fileExtension(displayName) : '';
  if (ext && ext.length <= 5) return ext.toUpperCase();
  if (doc.file_type && doc.file_type !== 'other') return doc.file_type.toUpperCase();
  return 'FILE';
}

function FileTypeIcon({ doc, ext }: { doc: TenderDocument; ext: string }) {
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
    return <FolderZipIcon sx={{ fontSize: 18, color: BRAND_ORANGE, flexShrink: 0 }} aria-hidden />;
  }
  return <InsertDriveFileIcon sx={{ fontSize: 18, color: 'text.disabled', flexShrink: 0 }} aria-hidden />;
}

function canViewInline(doc: TenderDocument, ext: string): boolean {
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
  // window.open must run synchronously inside the click handler, before any
  // await: Firefox and Safari only honour it as long as it's still inside
  // the user gesture that triggered the click, and a fetch in between (as
  // getDocumentBlob below does) ends that window. Chrome/Edge are lenient
  // enough that this bug hid there, which is why "view" only ever worked in
  // Chrome. Opening the tab first and filling it in once the blob is ready
  // works the same way in every browser.
  //
  // Deliberately no 'noopener': we need the handle back to navigate the tab
  // once the blob resolves. That's safe here because we only ever navigate
  // it to a blob: URL we just created ourselves, never to a remote page.
  const newTab = window.open('', '_blank');

  try {
    const blob = await getDocumentBlob(url, fileName);
    const objectUrl = URL.createObjectURL(blob);

    if (newTab && !newTab.closed) {
      newTab.location.href = objectUrl;
    } else {
      // Popup blocked outright (or the user closed the tab while we were
      // fetching) -- fall back to a download rather than doing nothing.
      await triggerDownload(url, fileName);
    }
  } catch (err) {
    newTab?.close();
    throw err;
  }
}

interface DocumentEntry {
  doc: TenderDocument;
  displayName: string;
  ext: string;
  typeLabel: string;
  // Position in the list as the API returned it, used for "Default" order.
  index: number;
}

type SortOrder = 'default' | 'name-asc' | 'name-desc' | 'type';

const SORT_OPTIONS: { value: SortOrder; label: string }[] = [
  { value: 'default', label: 'Default' },
  { value: 'name-asc', label: 'Name (A–Z)' },
  { value: 'name-desc', label: 'Name (Z–A)' },
  { value: 'type', label: 'File type' },
];

// numeric: true so "Addendum 2" sorts before "Addendum 10".
const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

function sortEntries(entries: DocumentEntry[], order: SortOrder): DocumentEntry[] {
  const byName = (a: DocumentEntry, b: DocumentEntry) =>
    collator.compare(a.displayName, b.displayName) || a.index - b.index;
  const sorted = [...entries];
  switch (order) {
    case 'name-asc':
      return sorted.sort(byName);
    case 'name-desc':
      return sorted.sort((a, b) => byName(b, a));
    case 'type':
      return sorted.sort((a, b) => collator.compare(a.typeLabel, b.typeLabel) || byName(a, b));
    default:
      return sorted.sort((a, b) => a.index - b.index);
  }
}

function DocumentRow({ entry }: { entry: DocumentEntry }) {
  const { doc, displayName, ext, typeLabel } = entry;
  const [downloading, setDownloading] = useState(false);
  const hasUrl = Boolean(doc.storage_url);
  const viewable = canViewInline(doc, ext);

  async function handleDownload() {
    if (!doc.storage_url) return;
    setDownloading(true);
    try {
      await triggerDownload(doc.storage_url, displayName);
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
        gap: 1.25,
        py: 0.5,
        px: 1.5,
        borderBottom: '1px solid',
        borderColor: 'secondary.main',
        '&:last-child': { borderBottom: 'none' },
        '&:hover': { bgcolor: 'action.hover' },
        minWidth: 0,
      }}
    >
      <FileTypeIcon doc={doc} ext={ext} />

      <Tooltip title={displayName} placement="top" enterDelay={600}>
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
          {displayName}
        </Typography>
      </Tooltip>

      <Typography
        variant="caption"
        aria-label={`File type ${typeLabel}`}
        sx={{
          flexShrink: 0,
          minWidth: 44,
          textAlign: 'center',
          px: 0.75,
          py: 0.125,
          border: '1px solid',
          borderColor: 'divider',
          borderRadius: 0.75,
          color: 'text.secondary',
          fontWeight: 600,
          fontSize: '0.6875rem',
          letterSpacing: '0.04em',
          lineHeight: 1.5,
        }}
      >
        {typeLabel}
      </Typography>

      <Box sx={{ display: 'flex', gap: 0.5, flexShrink: 0 }}>
        <Tooltip title={hasUrl ? `Download ${displayName}` : 'File not yet available'}>
          <span>
            <IconButton
              size="small"
              onClick={handleDownload}
              disabled={!hasUrl || downloading}
              aria-label={`Download ${displayName}`}
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

        {/* Non-PDF rows keep an empty slot so download buttons and type
            labels line up down the list. */}
        {!viewable && <Box sx={{ width: 36 }} aria-hidden />}
        {viewable && (
          <Tooltip title={hasUrl ? `View ${displayName}` : 'File not yet available'}>
            <span>
              <IconButton
                size="small"
                onClick={() => doc.storage_url && openInline(doc.storage_url, displayName)}
                disabled={!hasUrl}
                aria-label={`View ${displayName} in browser`}
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
  const [sortOrder, setSortOrder] = useState<SortOrder>('default');

  const entries = useMemo<DocumentEntry[]>(
    () =>
      documents
        .filter(d => Boolean(d.storage_url))
        .map((doc, index) => {
          const displayName = deriveDisplayName(doc);
          return {
            doc,
            displayName,
            ext: fileExtension(displayName),
            typeLabel: fileTypeLabel(doc, displayName),
            index,
          };
        }),
    [documents],
  );
  const sorted = useMemo(() => sortEntries(entries, sortOrder), [entries, sortOrder]);
  const sortLabelId = useId();

  return (
    <Box sx={{ mt: 2.5, mb: 1 }}>
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: 1,
          mb: 1,
        }}
      >
        <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
          Documents{entries.length > 0 && ` (${entries.length})`}
        </Typography>

        {entries.length > 1 && (
          <FormControl size="small" sx={{ minWidth: 150 }}>
            <InputLabel id={sortLabelId}>Sort by</InputLabel>
            <Select
              labelId={sortLabelId}
              value={sortOrder}
              label="Sort by"
              onChange={e => setSortOrder(e.target.value as SortOrder)}
              sx={{ fontSize: '0.875rem' }}
            >
              {SORT_OPTIONS.map(opt => (
                <MenuItem key={opt.value} value={opt.value} sx={{ fontSize: '0.875rem' }}>
                  {opt.label}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        )}
      </Box>

      {entries.length === 0 ? (
        <Typography variant="body2" color="text.secondary" sx={{ fontStyle: 'italic' }}>
          No documents available for this tender.
        </Typography>
      ) : (
        <Box
          component="ul"
          aria-label="Tender documents"
          sx={{
            listStyle: 'none',
            m: 0,
            p: 0,
            border: '1px solid',
            borderColor: 'secondary.main',
            borderRadius: 1,
            overflow: 'hidden',
          }}
        >
          {sorted.map(entry => (
            <DocumentRow
              key={entry.doc.document_id ?? `${entry.doc.file_name}-${entry.index}`}
              entry={entry}
            />
          ))}
        </Box>
      )}
    </Box>
  );
}
