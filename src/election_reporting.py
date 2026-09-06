import csv

county = 'Gratiot'

OFFICES = {
    'Straight Party Ticket': ('Straight Party', None),
    'United States Senator': ('U.S. Senate', None),
    'President/Vice President of the United States': ('President', None),
    'Representative in Congress 4th District': ('U.S. House', 4),
    'State Representative 70th District': ('State House', 70),
    'State Representative 93rd District': ('State House', 93),
    'Rep in State Legislature 99th District': ('State House', 99)
}

lines = open('/Users/dwillis/Downloads/tabula-Gratiot MI Results per Precinct Data report.tsv').readlines()
results = []

for line in lines:
    if len(line.split('\t')) < 3:
        office, district = OFFICES[line.strip()]
    elif line.split('\t')[0] == 'Total':
        continue
    elif line.split('\t')[0] == 'Precinct':
        candidates = line.split('\t')[1:]
    else:
        print(line)
        precinct = line.split('\t')[0]
        candidate_votes = line.split('\t')[1:]
        for candidate, votes in zip(candidates, candidate_votes):
            results.append([county, precinct, office, district, None, candidate.strip(), votes.strip()])

with open('20221108__mi__general__gratiot__precinct.csv', 'wt') as csvfile:
    writer = csv.writer(csvfile)
    writer.writerow(['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes'])
    writer.writerows(results)
