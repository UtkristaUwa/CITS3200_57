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
  Tooltip,
  Alert,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import FilterListIcon from '@mui/icons-material/FilterList';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import { getLocations } from '../lib/api';
import type { DefaultTenderStatus } from '../lib/tenderPreferences';

export interface TenderFilterBarProps {
  searchQuery: string;
  setSearchQuery: (val: string) => void;
  advancedSearch: boolean;
  setAdvancedSearch: (val: boolean) => void;
  showFilters: boolean;
  setShowFilters: (val: boolean) => void;
  jurisdiction: string;
  setJurisdiction: (val: string) => void;
  year: string;
  setYear: (val: string) => void;
  status: DefaultTenderStatus;
  setStatus: (val: DefaultTenderStatus) => void;
  minDate: string;
  setMinDate: (val: string) => void;
  maxDate: string;
  setMaxDate: (val: string) => void;
  filtersReady: boolean;
  defaultFiltersSaving: boolean;
  defaultFiltersLoadError: string | null;
  defaultFiltersSaveError: string | null;
  defaultFiltersSaved: boolean;
  handleSaveDefaultFilters: () => Promise<void>;
  handleResetFilters: () => void;
}

export default function TenderFilterBar(props: TenderFilterBarProps) {
  const [availableLocations, setAvailableLocations] = useState<string[]>([]);
  const locationOptions = props.jurisdiction
    && !availableLocations.includes(props.jurisdiction)
    ? [props.jurisdiction, ...availableLocations]
    : availableLocations;
  const standardYears = ['2026', '2025', '2024'];
  const yearOptions = props.year && !standardYears.includes(props.year)
    ? [props.year, ...standardYears]
    : standardYears;

  useEffect(() => {
    getLocations().then(setAvailableLocations);
  }, []);

  return (
    <Box
      sx={{
        display: { xs: 'contents', sm: 'block' },
        position: { sm: 'sticky' },
        top: { sm: 12 },
        zIndex: 10,
        mb: { sm: 2 },
        p: { sm: 1.5 },
        bgcolor: 'background.paper',
        borderRadius: 2,
        border: '1px solid',
        borderColor: 'secondary.main',
        boxShadow: (theme) => theme.palette.mode === 'light'
          ? '0 3px 10px rgba(36,45,50,0.08)'
          : '0 3px 10px rgba(0,0,0,0.24)',
      }}
    >
      <Box
        sx={{
          display: 'flex',
          gap: 1,
          alignItems: 'stretch',
          position: { xs: 'sticky', sm: 'static' },
          top: { xs: 8 },
          zIndex: { xs: 10 },
          mb: { xs: props.showFilters ? 0 : 2, sm: 0 },
          p: { xs: 1, sm: 0 },
          bgcolor: { xs: 'background.paper', sm: 'transparent' },
          borderRadius: { xs: 2, sm: 0 },
          border: { xs: '1px solid', sm: 'none' },
          borderColor: { xs: 'secondary.main' },
          boxShadow: (theme) => ({
            xs: theme.palette.mode === 'light'
              ? '0 3px 10px rgba(36,45,50,0.08)'
              : '0 3px 10px rgba(0,0,0,0.24)',
            sm: 'none',
          }),
          '& .MuiInputBase-root': { minHeight: { xs: 44, sm: 40 } },
        }}
      >
        <TextField
          fullWidth
          variant="outlined"
          size="small"
          placeholder="Search tenders..."
          value={props.searchQuery}
          onChange={(e) => props.setSearchQuery(e.target.value)}
          slotProps={{
            input: {
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon color="primary" />
                </InputAdornment>
              ),
            },
          }}
        />
        <Tooltip title="Toggle AI Semantic Search. When active, matches meaning and concepts (slower). When off, uses fast keyword search.">
          <Button
            variant={props.advancedSearch ? 'contained' : 'outlined'}
            startIcon={<AutoAwesomeIcon />}
            onClick={() => props.setAdvancedSearch(!props.advancedSearch)}
            sx={{
              flexShrink: 0,
              minHeight: { xs: 44, sm: 36 },
              textTransform: 'none',
              fontWeight: props.advancedSearch ? 600 : 500,
              ...(props.advancedSearch
                ? {
                    background: 'linear-gradient(135deg, #2D3AF1 0%, #6E78F7 100%)',
                    color: '#fff',
                    borderColor: 'transparent',
                    boxShadow: '0 2px 8px rgba(45, 58, 241, 0.35)',
                    '&:hover': {
                      background: 'linear-gradient(135deg, #232EC2 0%, #5C67ED 100%)',
                    },
                  }
                : {
                    color: 'text.secondary',
                    borderColor: 'divider',
                    '&:hover': {
                      borderColor: 'primary.main',
                      color: 'primary.main',
                    },
                  }),
            }}
          >
            Advanced
          </Button>
        </Tooltip>
        <Button
          variant={props.showFilters ? 'contained' : 'outlined'}
          startIcon={<FilterListIcon />}
          onClick={() => props.setShowFilters(!props.showFilters)}
          sx={{ flexShrink: 0, minHeight: { xs: 44, sm: 36 } }}
        >
          Filters
        </Button>
      </Box>

      <Collapse in={props.showFilters}>
        <Box
          sx={{
            display: 'flex',
            flexDirection: 'column',
            gap: 1.5,
            mt: { xs: 1, sm: 1.5 },
            mb: { xs: 2, sm: 0 },
            p: { xs: 1, sm: 0 },
            pt: 1.5,
            bgcolor: { xs: 'background.paper', sm: 'transparent' },
            border: { xs: '1px solid', sm: 'none' },
            borderTop: '1px solid',
            borderColor: 'secondary.main',
            borderRadius: { xs: 2, sm: 0 },
            '& .MuiInputBase-root': { minHeight: { xs: 44, sm: 40 } },
          }}
        >
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: 'minmax(0, 1fr)', sm: 'repeat(2, minmax(0, 1fr))', md: 'repeat(3, minmax(0, 1fr))' },
              gap: 1.5,
            }}
          >
            <FormControl size="small" fullWidth sx={{ minWidth: 0 }}>
              <InputLabel>Jurisdiction</InputLabel>
              <Select value={props.jurisdiction} label="Jurisdiction" onChange={(e) => props.setJurisdiction(e.target.value)}>
                <MenuItem value=""><em>All</em></MenuItem>
                {locationOptions.map((loc) => (
                  <MenuItem key={loc} value={loc}>{loc}</MenuItem>
                ))}
              </Select>
            </FormControl>

            <FormControl size="small" fullWidth sx={{ minWidth: 0 }}>
              <InputLabel>Year</InputLabel>
              <Select value={props.year} label="Year" onChange={(e) => props.setYear(e.target.value)}>
                <MenuItem value=""><em>All Time</em></MenuItem>
                {yearOptions.map((year) => (
                  <MenuItem key={year} value={year}>{year}</MenuItem>
                ))}
              </Select>
            </FormControl>

            <FormControl size="small" fullWidth sx={{ minWidth: 0 }}>
              <InputLabel>Status</InputLabel>
              <Select
                value={props.status}
                label="Status"
                onChange={(e) => props.setStatus(e.target.value as DefaultTenderStatus)}
              >
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
              gridTemplateColumns: { xs: 'minmax(0, 1fr)', sm: 'repeat(2, minmax(0, 1fr))' },
              gap: 1.5,
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
          </Box>

          {props.defaultFiltersLoadError && (
            <Alert severity="warning">
              {props.defaultFiltersLoadError}
            </Alert>
          )}
          {props.defaultFiltersSaveError && (
            <Alert severity="error">
              {props.defaultFiltersSaveError}
            </Alert>
          )}
          {props.defaultFiltersSaved && (
            <Alert severity="success">
              Current filters saved as your default.
            </Alert>
          )}

          <Box
            sx={{
              display: 'flex',
              flexDirection: { xs: 'column', sm: 'row' },
              justifyContent: 'space-between',
              alignItems: { xs: 'stretch', sm: 'center' },
              gap: 1,
              mt: 1,
            }}
          >
            <Button
              size="small"
              variant="contained"
              onClick={() => void props.handleSaveDefaultFilters()}
              disabled={!props.filtersReady || props.defaultFiltersSaving}
              sx={{ minHeight: { xs: 44, sm: 30 } }}
            >
              {props.defaultFiltersSaving ? 'Saving...' : 'Save as Default'}
            </Button>
            <Button size="small" color="primary" onClick={props.handleResetFilters} sx={{ minHeight: { xs: 44, sm: 30 } }}>
              Clear All Filters
            </Button>
          </Box>
        </Box>
      </Collapse>
    </Box>
  );
}
