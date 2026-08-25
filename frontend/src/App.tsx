import { useState } from 'react';
import {
  AppBar, Toolbar, Typography, Button, Container, Card, CardContent,
  CardActions, Collapse, Box, Chip, Link
} from '@mui/material';

// Mock Data for the tender skeleton
interface Tender {
  id: number;
  title: string;
  isNew: boolean;
  isFavorite: boolean;
  source: string;
}

const initialTenders: Tender[] = [
  { id: 1, title: "Tender Title", isNew: true, isFavorite: false, source: "https://example.com/tender-1" },
  { id: 2, title: "Tender Title", isNew: false, isFavorite: false, source: "https://example.com/tender-2" },
  { id: 3, title: "Tender Title", isNew: false, isFavorite: false, source: "https://example.com/tender-3" },
];

// Single Tender Card Component
function TenderCard({ tender, onToggleFavorite }: { tender: Tender; onToggleFavorite: (id: number) => void }) {
  // State to control the expand/collapse action
  const [expanded, setExpanded] = useState(false);

  return (
    <Card sx={{ mb: 2, border: '1px solid #ccc', boxShadow: 'none' }}>
      <CardContent>
        {/* ==========================================
            1. Collapsed State (Default Display)
            Contains Title, ATM ID, Closing Date, Agency, Description
            ========================================== */}
        
        {/* Title and Badges */}
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1 }}>
          <Typography variant="h6" component="div" sx={{ textAlign: 'left' }}>
            {tender.title}
          </Typography>
          <Box sx={{ display: 'flex', gap: 1 }}>
            {tender.isNew && <Chip label="NEW" color="primary" size="small" />}
            <Chip
              label="FAVORITE"
              color={tender.isFavorite ? 'warning' : 'default'}
              size="small"
              onClick={() => onToggleFavorite(tender.id)}
              sx={{ cursor: 'pointer' }}
            />
          </Box>
        </Box>

        {/* Outer layer information (Left Aligned, Reordered) */}
        <Typography variant="body2" sx={{ textAlign: 'left', mb: 0.5 }}>
          <strong>ATM ID:</strong> 
        </Typography>
        <Typography variant="body2" sx={{ textAlign: 'left', mb: 0.5 }}>
          <strong>Closing Date:</strong> 
        </Typography>
        <Typography variant="body2" sx={{ textAlign: 'left', mb: 0.5 }}>
          <strong>Agency:</strong> 
        </Typography>
        <Typography variant="body2" sx={{ textAlign: 'left', mb: 0.5 }}>
          <strong>Description:</strong> 
        </Typography>
        <Typography variant="body2" sx={{ textAlign: 'left', mb: 1.5 }}>
          <strong>Source:</strong>{' '}
          <Link href={tender.source} target="_blank" rel="noopener noreferrer">
            {tender.source}
          </Link>
        </Typography>

        {/* ==========================================
            2. Expanded State (After clicking View More)
            Contains Summary, Opening Date, Closing Date, Monetary, ATM ID, Location, Agency, Description
            ========================================== */}
        <Collapse in={expanded} timeout="auto" unmountOnExit>
          <Box sx={{ mt: 2, p: 2, bgcolor: '#f9f9f9', borderRadius: 1, textAlign: 'left' }}>
            
            <Typography variant="body2" component="p" sx={{ mb: 2 }}>
              <strong>Summary:</strong> 
            </Typography>
            
            <Typography variant="body2" component="p" sx={{ mb: 2 }}>
              <strong>Opening Date:</strong> 
            </Typography>
            
            <Typography variant="body2" component="p" sx={{ mb: 2 }}>
              <strong>Closing Date:</strong> 
            </Typography>
            
            <Typography variant="body2" component="p" sx={{ mb: 2 }}>
              <strong>Monetary:</strong> 
            </Typography>
            
            <Typography variant="body2" component="p" sx={{ mb: 2 }}>
              <strong>ATM ID:</strong> 
            </Typography>
            
            <Typography variant="body2" component="p" sx={{ mb: 2 }}>
              <strong>Location:</strong> 
            </Typography>

            <Typography variant="body2" component="p" sx={{ mb: 2 }}>
              <strong>Agency:</strong> 
            </Typography>

            {/* Description at the bottom with a reserved area for details */}
            <Typography variant="body2" sx={{ mt: 2 }}>
              <strong>Description:</strong> 
            </Typography>
            <Box 
              sx={{ 
                mt: 1, 
                p: 2, 
                border: '1px dashed #ccc', 
                borderRadius: 1, 
                minHeight: '80px',
                color: '#888'
              }}
            >
              {/* Placeholder text for detailed description */}
              Detailed explanation area reserved here...
            </Box>

          </Box>
        </Collapse>
      </CardContent>

      {/* View More / View Less button centered at the bottom */}
      <CardActions sx={{ justifyContent: 'center' }}>
        <Button size="small" onClick={() => setExpanded(!expanded)}>
          {expanded ? 'View Less' : 'View More'}
        </Button>
      </CardActions>
    </Card>
  );
}

// Main Page Component
export default function BasicSkeletonApp() {
  const [tenders, setTenders] = useState<Tender[]>(initialTenders);

  const handleToggleFavorite = (id: number) => {
    setTenders(prev =>
      [...prev]
        .map(t => (t.id === id ? { ...t, isFavorite: !t.isFavorite } : t))
        .sort((a, b) => Number(b.isFavorite) - Number(a.isFavorite))
    );
  };

  return (
    <Box sx={{ flexGrow: 1 }}>
      {/* Top Navigation Bar */}
      <AppBar position="static" color="default" sx={{ mb: 3 }}>
        <Toolbar>
          <Typography variant="h6" sx={{ flexGrow: 1, textAlign: 'left' }}>
            TenderAI
          </Typography>
          <Button color="inherit">Feed</Button>
          <Button color="inherit">Starred</Button>
          <Button color="inherit">Admin</Button>
        </Toolbar>
      </AppBar>

      {/* Main Content: Render Cards */}
      <Container maxWidth="md">
        {tenders.map((tender) => (
          <TenderCard key={tender.id} tender={tender} onToggleFavorite={handleToggleFavorite} />
        ))}
      </Container>
    </Box>
  );
}