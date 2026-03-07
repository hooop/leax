#include <stdlib.h>
#include <string.h>


void	init_data(void)
{
	char	*tmp;

	tmp = malloc(128);
	strcpy(tmp, "temporary data");
}

int	main(void)
{
	init_data();
	return (0);
}
