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
    return (
      <Box sx={{ mb: 4, p: 2, bgcolor: '#ffffff', borderRadius: 2, border: '1px solid #e0e0e0' }}>
        <Box sx={{ display: 'flex', gap: 2, alignItems: 'center' }}>
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
            sx={{ flexShrink: 0 }}
          >
            Filters
          </Button>
        </Box>
  
        <Collapse in={props.showFilters}>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 2, pt: 2, borderTop: '1px dashed #ccc' }}>
            <Box sx={{ display: 'flex', gap: 2 }}>
              <FormControl size="small" fullWidth>
                <InputLabel>Jurisdiction</InputLabel>
                <Select value={props.jurisdiction} label="Jurisdiction" onChange={(e) => props.setJurisdiction(e.target.value)}>
                  <MenuItem value=""><em>All</em></MenuItem>
                  <MenuItem value="Western Australia">Western Australia (WA)</MenuItem>
                  <MenuItem value="Victoria">Victoria (VIC)</MenuItem>
                  <MenuItem value="New South Wales">New South Wales (NSW)</MenuItem>
                </Select>
              </FormControl>
  
              <FormControl size="small" fullWidth>
                <InputLabel>Year</InputLabel>
                <Select value={props.year} label="Year" onChange={(e) => props.setYear(e.target.value)}>
                  <MenuItem value=""><em>All Time</em></MenuItem>
                  <MenuItem value="2026">2026</MenuItem>
                  <MenuItem value="2025">2025</MenuItem>
                  <MenuItem value="2024">2024</MenuItem>
                </Select>
              </FormControl>
  
              <FormControl size="small" fullWidth>
                <InputLabel>Category</InputLabel>
                <Select value={props.category} label="Category" onChange={(e) => props.setCategory(e.target.value)}>
                  <MenuItem value=""><em>All</em></MenuItem>
                  <MenuItem value="tender">Tender</MenuItem>
                  <MenuItem value="rfq">RFQ</MenuItem>
                  <MenuItem value="eoi">EOI</MenuItem>
                  <MenuItem value="grant">Grant</MenuItem>
                </Select>
              </FormControl>
  
              <FormControl size="small" fullWidth>
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
  
            <Box sx={{ display: 'flex', gap: 2 }}>
              <TextField
                size="small"
                fullWidth
                type="date"
                label="Closing After"
                slotProps={{ inputLabel: { shrink: true } }}
                value={props.minDate}
                onChange={(e) => props.setMinDate(e.target.value)}
              />
              <TextField
                size="small"
                fullWidth
                type="date"
                label="Closing Before"
                slotProps={{ inputLabel: { shrink: true } }}
                value={props.maxDate}
                onChange={(e) => props.setMaxDate(e.target.value)}
              />
              <TextField
                size="small"
                fullWidth
                type="number"
                label="Min Value ($)"
                value={props.minValue}
                onChange={(e) => props.setMinValue(e.target.value)}
              />
              <TextField
                size="small"
                fullWidth
                type="number"
                label="Max Value ($)"
                value={props.maxValue}
                onChange={(e) => props.setMaxValue(e.target.value)}
              />
            </Box>
  
            <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 1 }}>
              <Button size="small" color="inherit" onClick={props.handleResetFilters}>
                Clear All Filters
              </Button>
            </Box>
          </Box>
        </Collapse>
      </Box>
    );
  }