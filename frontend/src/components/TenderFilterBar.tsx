import { useState, useEffect } from 'react';
import {
  Box,
  TextField,
  InputAdornment,
  Button,
  Collapse,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import FilterListIcon from '@mui/icons-material/FilterList';
import { getLocations } from '../lib/api';

export interface TenderFilterBarProps {
  searchQuery: string;
  setSearchQuery: (val: string) => void;
  showFilters: boolean;
  setShowFilters: (val: boolean) => void;
  jurisdiction: string;
  setJurisdiction: (val: string) => void;
  year: string;
  setYear: (val: string) => void;
  category: string;
  setCategory: (val: string) => void;
  status: string;
  setStatus: (val: string) => void;
  minDate: string;
  setMinDate: (val: string) => void;
  maxDate: string;
  setMaxDate: (val: string) => void;
  minValue: string;
  setMinValue: (val: string) => void;
  maxValue: string;
  setMaxValue: (val: string) => void;
  handleResetFilters: () => void;
}

export default function TenderFilterBar(props: TenderFilterBarProps) {
  const [availableLocations, setAvailableLocations] = useState<string[]>([]);

  useEffect(() => {
    getLocations().then(setAvailableLocations);
  }, []);

  return (
    <Box sx={{ mb: 4, p: { xs: 1.5, sm: 2 }, bgcolor: 'background.paper', borderRadius: 2, border: '1px solid', borderColor: 'divider' }}>
      <Box
        sx={{
          display: 'flex',
          flexDirection: { xs: 'column', sm: 'row' },
          gap: 2,
          alignItems: 'stretch',
          '& .MuiInputBase-root': { minHeight: { xs: 44, sm: 40 } },
        }}
      >
        <TextField
          fullWidth
          variant="outlined"
          size="small"
          placeholder="Search tenders by keyword..."
          value={props.searchQuery}
          onChange={(e) => props.setSearchQuery(e.target.value)}
          slotProps={{
            input: {
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon color="action" />
                </InputAdornment>
              ),
            },
          }}
        />
        <Button
          variant={props.showFilters ? 'contained' : 'outlined'}
          startIcon={<FilterListIcon />}
          onClick={() => props.setShowFilters(!props.showFilters)}
          sx={{ flexShrink: 0, width: { xs: '100%', sm: 'auto' }, minHeight: { xs: 44, sm: 36 } }}
        >
          Filters
        </Button>
      </Box>

      <Collapse in={props.showFilters}>
        <Box
          sx={{
            display: 'flex',
            flexDirection: 'column',
            gap: 2,
            mt: 2,
            pt: 2,
            borderTop: '1px dashed',
            borderColor: 'divider',
            '& .MuiInputBase-root': { minHeight: { xs: 44, sm: 40 } },
          }}
        >
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: 'minmax(0, 1fr)', sm: 'repeat(2, minmax(0, 1fr))', md: 'repeat(4, minmax(0, 1fr))' },
              gap: 2,
            }}
          >
            <FormControl size="small" fullWidth sx={{ minWidth: 0 }}>
              <InputLabel>Jurisdiction</InputLabel>
              <Select value={props.jurisdiction} label="Jurisdiction" onChange={(e) => props.setJurisdiction(e.target.value)}>
                <MenuItem value=""><em>All</em></MenuItem>
                {availableLocations.map((loc) => (
                  <MenuItem key={loc} value={loc}>{loc}</MenuItem>
                ))}
              </Select>
            </FormControl>

            <FormControl size="small" fullWidth sx={{ minWidth: 0 }}>
              <InputLabel>Year</InputLabel>
              <Select value={props.year} label="Year" onChange={(e) => props.setYear(e.target.value)}>
                <MenuItem value=""><em>All Time</em></MenuItem>
                <MenuItem value="2026">2026</MenuItem>
                <MenuItem value="2025">2025</MenuItem>
                <MenuItem value="2024">2024</MenuItem>
              </Select>
            </FormControl>

            <FormControl size="small" fullWidth sx={{ minWidth: 0 }}>
              <InputLabel>Category</InputLabel>
              <Select value={props.category} label="Category" onChange={(e) => props.setCategory(e.target.value)}>
                <MenuItem value=""><em>All</em></MenuItem>
                <MenuItem value="tender">Tender</MenuItem>
                <MenuItem value="rfq">RFQ</MenuItem>
                <MenuItem value="eoi">EOI</MenuItem>
                <MenuItem value="grant">Grant</MenuItem>
              </Select>
            </FormControl>

            <FormControl size="small" fullWidth sx={{ minWidth: 0 }}>
              <InputLabel>Status</InputLabel>
              <Select value={props.status} label="Status" onChange={(e) => props.setStatus(e.target.value)}>
                <MenuItem value=""><em>All</em></MenuItem>
                <MenuItem value="open">Open</MenuItem>
                <MenuItem value="closed">Closed</MenuItem>
                <MenuItem value="awarded">Awarded</MenuItem>
                <MenuItem value="unknown">Unknown</MenuItem>
              </Select>
            </FormControl>
          </Box>

          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: 'minmax(0, 1fr)', sm: 'repeat(2, minmax(0, 1fr))', md: 'repeat(4, minmax(0, 1fr))' },
              gap: 2,
            }}
          >
            <TextField
              size="small"
              fullWidth
              type="date"
              label="Closing After"
              slotProps={{ inputLabel: { shrink: true } }}
              value={props.minDate}
              onChange={(e) => props.setMinDate(e.target.value)}
              sx={{ minWidth: 0 }}
            />
            <TextField
              size="small"
              fullWidth
              type="date"
              label="Closing Before"
              slotProps={{ inputLabel: { shrink: true } }}
              value={props.maxDate}
              onChange={(e) => props.setMaxDate(e.target.value)}
              sx={{ minWidth: 0 }}
            />
            <TextField
              size="small"
              fullWidth
              type="number"
              label="Min Value ($)"
              value={props.minValue}
              onChange={(e) => props.setMinValue(e.target.value)}
              sx={{ minWidth: 0 }}
            />
            <TextField
              size="small"
              fullWidth
              type="number"
              label="Max Value ($)"
              value={props.maxValue}
              onChange={(e) => props.setMaxValue(e.target.value)}
              sx={{ minWidth: 0 }}
            />
          </Box>

          <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 1 }}>
            <Button size="small" color="inherit" onClick={props.handleResetFilters} sx={{ minHeight: { xs: 44, sm: 30 } }}>
              Clear All Filters
            </Button>
          </Box>
        </Box>
      </Collapse>
    </Box>
  );
}
