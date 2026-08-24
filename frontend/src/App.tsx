import React, { useState } from 'react';
import {
  AppBar, Toolbar, Typography, Button, Container, Card, CardContent,
  CardActions, Box, Chip, Link, Dialog, DialogTitle, DialogContent, DialogActions
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
  // State to control the Modal/Dialog open state
  const [open, setOpen] = useState(false);

  const handleOpen = () => setOpen(true);
  const handleClose = () => setOpen(false);

  return (
    <>
      <Card sx={{ mb: 2, border: '1px solid #ccc', boxShadow: 'none' }}>
        <CardContent>
          {/* ==========================================
              1. Outer Card State (Default Display)
              Kept minimal to save space, featuring the 1-3 line summary
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

          {/* Ramon's requested 1-3 line AI summary at the top */}
          <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'left', mb: 2, fontStyle: 'italic' }}>
            AI Summary: This is a 1-3 line high-level summary of the tender opportunity, highlighting the core problem and expectations.
          </Typography>

          {/* Core Outer Information */}
          <Typography variant="body2" sx={{ textAlign: 'left', mb: 0.5 }}>
            <strong>ATM ID:</strong> {tender.id}
          </Typography>
          <Typography variant="body2" sx={{ textAlign: 'left', mb: 0.5 }}>
            <strong>Closing Date:</strong> TBD
          </Typography>
          <Typography variant="body2" sx={{ textAlign: 'left', mb: 0.5 }}>
            <strong>Agency:</strong> Example Agency
          </Typography>
        </CardContent>

        {/* View More button to trigger Dialog */}
        <CardActions sx={{ justifyContent: 'center' }}>
          <Button size="small" onClick={handleOpen}>
            View More
          </Button>
        </CardActions>
      </Card>

      {/* ==========================================
          2. Expanded Modal State (After clicking View More)
          Pops up over the screen with a gray backdrop
          ========================================== */}
      <Dialog 
        open={open} 
        onClose={handleClose} 
        maxWidth="md" 
        fullWidth
        scroll="paper"
      >
        <DialogTitle sx={{ fontWeight: 'bold' }}>
          {tender.title}
        </DialogTitle>
        <DialogContent dividers>
          <Box sx={{ textAlign: 'left' }}>
            
            <Typography variant="body2" paragraph>
              <strong>Detailed AI Summary:</strong> Full extracted text goes here...
            </Typography>
            
            <Typography variant="body2" paragraph>
              <strong>Opening Date:</strong> TBD
            </Typography>
            
            <Typography variant="body2" paragraph>
              <strong>Closing Date:</strong> TBD
            </Typography>
            
            <Typography variant="body2" paragraph>
              <strong>Monetary:</strong> TBD
            </Typography>
            
            <Typography variant="body2" paragraph>
              <strong>ATM ID:</strong> {tender.id}
            </Typography>
            
            <Typography variant="body2" paragraph>
              <strong>Location:</strong> TBD
            </Typography>

            <Typography variant="body2" paragraph>
              <strong>Agency:</strong> Example Agency
            </Typography>

            <Typography variant="body2" paragraph>
              <strong>Source:</strong>{' '}
              <Link href={tender.source} target="_blank" rel="noopener noreferrer">
                {tender.source}
              </Link>
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
                minHeight: '150px', // Taller box for easier scrolling
                color: '#888'
              }}
            >
              Detailed explanation area reserved here. This space allows for easy scrolling of long text inside the modal...
            </Box>
          </Box>
        </DialogContent>
        
        {/* Close Button at the bottom of the modal */}
        <DialogActions>
          <Button onClick={handleClose} color="primary">
            Close
          </Button>
        </DialogActions>
      </Dialog>
    </>
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